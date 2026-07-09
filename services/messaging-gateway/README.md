# RestoMind Messaging Gateway

Node.js provider service for the Messaging Gateway MVP. The first provider is `whatsapp_baileys`.

## Run Locally

```bash
cd services/messaging-gateway
npm install
cp .env.example .env
npm start
```

Or with the full local stack:

```bash
docker compose up --build app worker messaging-gateway
```

Required environment:

```text
RESTOMIND_API_URL=http://localhost:8000
RESTOMIND_GATEWAY_SECRET=the-same-secret-as-MESSAGING_GATEWAY_SECRET
SESSION_ROOT=./sessions
```

RestoMind backend needs:

```text
MESSAGING_GATEWAY_URL=http://localhost:3107
MESSAGING_GATEWAY_SECRET=the-same-secret-as-RESTOMIND_GATEWAY_SECRET
```

## Flow

1. Admin creates a `whatsapp_baileys` connection in RestoMind.
2. RestoMind calls `POST /v1/connections/start` on this service.
3. Baileys emits a QR code.
4. This service sends the QR/status to `POST /api/channels/gateway/connections/status`.
5. Incoming WhatsApp messages are normalized and sent to `POST /api/channels/inbound`.
6. RestoMind processes the message through the existing AI/order pipeline.
7. Outbound replies are sent back to `POST /v1/send`.

## Notes

- Sessions are stored under `SESSION_ROOT` using the connection `session_ref`.
- If a WhatsApp session expires, RestoMind receives `expired`/`qr_required` and the admin can reconnect.
- This service must not know about menu, orders, AI prompts, CRM, or business rules.
