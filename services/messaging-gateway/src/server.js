import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState
} from '@whiskeysockets/baileys'
import { Boom } from '@hapi/boom'
import express from 'express'
import fs from 'node:fs/promises'
import path from 'node:path'
import pino from 'pino'
import QRCode from 'qrcode'
import qrcode from 'qrcode-terminal'

const PORT = Number(process.env.PORT || 3107)
const RESTOMIND_API_URL = (process.env.RESTOMIND_API_URL || 'http://localhost:8000').replace(/\/+$/, '')
const RESTOMIND_GATEWAY_SECRET = process.env.RESTOMIND_GATEWAY_SECRET || ''
const SESSION_ROOT = process.env.SESSION_ROOT || path.join(process.cwd(), 'sessions')
const AUTO_START_CONNECTION_IDS = (process.env.AUTO_START_CONNECTION_IDS || '')
  .split(',')
  .map((x) => x.trim())
  .filter(Boolean)

const logger = pino({ level: process.env.LOG_LEVEL || 'info' })
const app = express()
app.use(express.json({ limit: '2mb' }))

app.use('/v1', (req, res, next) => {
  if (!RESTOMIND_GATEWAY_SECRET) {
    next()
    return
  }
  const incoming = String(req.get('X-RestoMind-Gateway-Secret') || '')
  if (incoming !== RESTOMIND_GATEWAY_SECRET) {
    res.status(401).json({ ok: false, error: 'invalid_gateway_secret' })
    return
  }
  next()
})

const sockets = new Map()

function headers() {
  return RESTOMIND_GATEWAY_SECRET ? { 'X-RestoMind-Gateway-Secret': RESTOMIND_GATEWAY_SECRET } : {}
}

async function postToRestoMind(pathname, payload) {
  const response = await fetch(`${RESTOMIND_API_URL}${pathname}`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...headers()
    },
    body: JSON.stringify(payload)
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`RestoMind ${pathname} failed: ${response.status} ${text.slice(0, 500)}`)
  }
  const text = await response.text()
  return text ? JSON.parse(text) : {}
}

async function getFromRestoMind(pathname) {
  const response = await fetch(`${RESTOMIND_API_URL}${pathname}`, {
    method: 'GET',
    headers: headers()
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`RestoMind ${pathname} failed: ${response.status} ${text.slice(0, 500)}`)
  }
  const text = await response.text()
  return text ? JSON.parse(text) : {}
}

function connectionSessionPath(connectionId, sessionRef = '') {
  const safeId = String(connectionId).replace(/[^a-zA-Z0-9_-]/g, '_')
  const safeRef = String(sessionRef || '').replace(/[^a-zA-Z0-9/_-]/g, '_')
  if (safeRef) {
    return path.join(SESSION_ROOT, safeRef)
  }
  return path.join(SESSION_ROOT, `connection_${safeId}`)
}

function getTextMessage(message) {
  const content = message?.message || {}
  return (
    content.conversation ||
    content.extendedTextMessage?.text ||
    content.imageMessage?.caption ||
    content.videoMessage?.caption ||
    ''
  )
}

function senderPhoneFromJid(jid = '') {
  return String(jid).split('@')[0].replace(/\D+/g, '')
}

function baileysStatusToDelivery(status) {
  const n = Number(status)
  if (n >= 4) return 'read'
  if (n === 3) return 'delivered'
  if (n === 2) return 'sent'
  return ''
}

async function publishConnectionStatus(connectionId, status, extra = {}) {
  try {
    await postToRestoMind('/api/channels/gateway/connections/status', {
      channel_connection_id: Number(connectionId),
      provider: 'whatsapp_baileys',
      status,
      ...extra
    })
  } catch (error) {
    logger.warn({ err: error, connectionId, status }, 'failed to publish connection status')
  }
}

async function publishInbound(connectionId, msg) {
  const remoteJid = msg?.key?.remoteJid || ''
  const fromMe = Boolean(msg?.key?.fromMe)
  if (!remoteJid || fromMe) return

  const text = getTextMessage(msg)
  if (!text.trim()) return

  const participant = msg?.key?.participant || remoteJid
  const phone = senderPhoneFromJid(participant)
  const externalMessageId = msg?.key?.id || ''
  await postToRestoMind('/api/channels/inbound', {
    trace_id: externalMessageId ? `baileys:${externalMessageId}` : '',
    correlation_id: '',
    idempotency_key: externalMessageId ? `whatsapp_baileys:${connectionId}:${externalMessageId}` : '',
    provider: 'whatsapp_baileys',
    channel_connection_id: Number(connectionId),
    external_chat_id: remoteJid,
    external_message_id: externalMessageId,
    sender: {
      external_id: phone,
      phone: phone ? `+${phone}` : '',
      display_name: msg?.pushName || ''
    },
    message: {
      type: 'text',
      text,
      payload: {},
      metadata: {
        participant,
        from_me: fromMe,
        timestamp: msg?.messageTimestamp || null
      }
    },
    received_at: new Date().toISOString()
  })
}

async function stopConnection(connectionId) {
  const key = String(connectionId)
  const entry = sockets.get(key)
  sockets.delete(key)
  try {
    entry?.sock?.end?.()
  } catch (error) {
    logger.warn({ err: error, connectionId: key }, 'socket end failed')
  }
}

async function startConnection(connectionId, sessionRef = '', options = {}) {
  const key = String(connectionId)
  if (options.force) {
    await stopConnection(connectionId)
  }
  if (sockets.has(key)) {
    return { ok: true, reused: true }
  }

  const authPath = connectionSessionPath(connectionId, sessionRef)
  await fs.mkdir(authPath, { recursive: true })
  const { state, saveCreds } = await useMultiFileAuthState(authPath)
  const { version } = await fetchLatestBaileysVersion()

  const sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    markOnlineOnConnect: true,
    logger: logger.child({ connectionId })
  })

  sockets.set(key, { sock, sessionRef, authPath })
  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update
    if (qr) {
      qrcode.generate(qr, { small: true })
      const qrDataUrl = await QRCode.toDataURL(qr, { margin: 2, scale: 6 })
      await publishConnectionStatus(connectionId, 'qr_required', {
        qr,
        session_ref: sessionRef,
        health: { provider: 'whatsapp_baileys', health: 'needs_reconnect', qr_data_url: qrDataUrl }
      })
    }

    if (connection === 'open') {
      await publishConnectionStatus(connectionId, 'connected', {
        session_ref: sessionRef,
        health: { provider: 'whatsapp_baileys', health: 'works' }
      })
      logger.info({ connectionId }, 'baileys connected')
    }

    if (connection === 'close') {
      sockets.delete(key)
      const statusCode = lastDisconnect?.error instanceof Boom
        ? lastDisconnect.error.output?.statusCode
        : undefined
      const loggedOut = statusCode === DisconnectReason.loggedOut
      await publishConnectionStatus(connectionId, loggedOut ? 'expired' : 'disconnected', {
        session_ref: sessionRef,
        error: lastDisconnect?.error?.message || '',
        health: { provider: 'whatsapp_baileys', health: loggedOut ? 'blocked' : 'degraded', status_code: statusCode || null }
      })
      if (!loggedOut) {
        setTimeout(() => {
          startConnection(connectionId, sessionRef).catch((error) => {
            logger.error({ err: error, connectionId }, 'baileys reconnect failed')
          })
        }, 5000)
      }
    }
  })

  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const msg of messages || []) {
      try {
        await publishInbound(connectionId, msg)
      } catch (error) {
        logger.warn({ err: error, connectionId }, 'failed to publish inbound message')
      }
    }
  })

  sock.ev.on('messages.update', async (updates) => {
    for (const item of updates || []) {
      const externalMessageId = item?.key?.id || ''
      const status = baileysStatusToDelivery(item?.update?.status)
      if (!externalMessageId || !status) continue
      try {
        await postToRestoMind('/api/channels/gateway/messages/status', {
          channel_connection_id: Number(connectionId),
          provider: 'whatsapp_baileys',
          external_message_id: externalMessageId,
          status,
          raw: { status: item.update?.status || null }
        })
      } catch (error) {
        logger.warn({ err: error, connectionId, externalMessageId, status }, 'failed to publish message update')
      }
    }
  })

  return { ok: true, session_ref: sessionRef, auth_path: authPath }
}

app.get('/health', (_req, res) => {
  res.json({
    ok: true,
    provider: 'whatsapp_baileys',
    active_connections: sockets.size
  })
})

app.post('/v1/connections/start', async (req, res) => {
  const connectionId = Number(req.body?.channel_connection_id)
  if (!connectionId) {
    res.status(400).json({ ok: false, error: 'channel_connection_id_required' })
    return
  }
  try {
    const out = await startConnection(connectionId, req.body?.session_ref || '', {
      force: Boolean(req.body?.force)
    })
    res.json(out)
  } catch (error) {
    await publishConnectionStatus(connectionId, 'error', { error: error.message || String(error) })
    res.status(500).json({ ok: false, error: error.message || String(error) })
  }
})

app.post('/v1/connections/:id/stop', async (req, res) => {
  const key = String(req.params.id)
  await stopConnection(key)
  await publishConnectionStatus(key, 'disabled', { health: { provider: 'whatsapp_baileys', health: 'blocked' } })
  res.json({ ok: true })
})

app.post('/v1/send', async (req, res) => {
  const connectionId = Number(req.body?.channel_connection_id)
  const externalChatId = String(req.body?.external_chat_id || '')
  const text = String(req.body?.message?.text || '')
  const channelMessageId = req.body?.channel_message_id ? Number(req.body.channel_message_id) : null
  const entry = sockets.get(String(connectionId))
  if (!entry?.sock) {
    res.status(409).json({ ok: false, error: 'connection_not_active' })
    return
  }
  if (!externalChatId || !text) {
    res.status(400).json({ ok: false, error: 'external_chat_id_and_text_required' })
    return
  }

  try {
    const result = await entry.sock.sendMessage(externalChatId, { text })
    const externalMessageId = result?.key?.id || ''
    await postToRestoMind('/api/channels/gateway/messages/status', {
      channel_message_id: channelMessageId,
      channel_connection_id: connectionId,
      provider: 'whatsapp_baileys',
      external_message_id: externalMessageId,
      status: 'sent',
      raw: result || {}
    })
    res.json({ ok: true, external_message_id: externalMessageId })
  } catch (error) {
    await postToRestoMind('/api/channels/gateway/messages/status', {
      channel_message_id: channelMessageId,
      channel_connection_id: connectionId,
      provider: 'whatsapp_baileys',
      status: 'failed',
      error_code: error.name || 'send_failed',
      error_message: error.message || String(error),
      raw: {}
    }).catch(() => {})
    res.status(500).json({ ok: false, error: error.message || String(error) })
  }
})

app.listen(PORT, async () => {
  logger.info({ PORT, RESTOMIND_API_URL, SESSION_ROOT }, 'messaging gateway listening')
  try {
    const rows = await getFromRestoMind('/api/channels/gateway/connections?provider=whatsapp_baileys')
    for (const conn of Array.isArray(rows) ? rows : []) {
      if (conn?.id && conn?.status !== 'disabled') {
        startConnection(conn.id, conn.session_ref || '').catch((error) => {
          logger.error({ err: error, connectionId: conn.id }, 'backend connection auto-start failed')
        })
      }
    }
  } catch (error) {
    logger.warn({ err: error }, 'failed to load connections from RestoMind')
  }
  for (const id of AUTO_START_CONNECTION_IDS) {
    startConnection(id).catch((error) => {
      logger.error({ err: error, connectionId: id }, 'auto-start failed')
    })
  }
})
