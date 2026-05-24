/**
 * RestoMind admin panel — Alpine x-data="adminApp()".
 * Chart.js загружается лениво только когда график становится нужен.
 */
'use strict';

let adminChartJsLoadPromise = null;
function adminEnsureChartJs() {
    if (typeof window !== 'undefined' && window.Chart) return Promise.resolve(window.Chart);
    if (adminChartJsLoadPromise) return adminChartJsLoadPromise;
    adminChartJsLoadPromise = new Promise((resolve, reject) => {
        const existing = document.querySelector('script[data-admin-chartjs]');
        if (existing) {
            existing.addEventListener('load', () => resolve(window.Chart), { once: true });
            existing.addEventListener('error', reject, { once: true });
            return;
        }
        const script = document.createElement('script');
        script.src = '/static/vendor/chart.umd.min.js';
        script.async = true;
        script.defer = true;
        script.dataset.adminChartjs = 'true';
        script.onload = () => resolve(window.Chart);
        script.onerror = () => reject(new Error('Chart.js failed to load from local vendor'));
        document.head.appendChild(script);
    });
    return adminChartJsLoadPromise;
}

/**
 * Экземпляры Chart.js вне реактивного объекта Alpine — иначе Proxy может ломать внутренние ссылки на canvas.
 */
const charts = {
    dashboard: null,
    analytics: null,
    analyticsSparks: {},
};

function adminDefaultSettingsEnv() {
    return {
        app_version: '—',
        db_mode: '',
        redis_backend: '',
        app_debug: false,
        integrations: {
            iiko: {},
            whatsapp: {},
            telegram: {},
            openai: {},
            gemini: {},
            ai_provider: 'openai',
            public_base_url_set: false,
        },
        chat_log_retention: null,
    };
}

function adminDefaultDashRoiSummary() {
    return { narrative: '', metrics: null, achievements: [] };
}

function adminDefaultReadinessPayload() {
    return { checks: [], links: {}, generated_at: '' };
}

/** Пустые структуры вместо null — Alpine не падает на shiftState.metrics до fetch. */
function adminDefaultShiftState() {
    return {
        state: null,
        metrics: {
            risk_kzt: 0,
            saved_today_kzt: 0,
            at_risk_count: 0,
            queue_size: 0,
            queue_size_active: 0,
            excluded_skip: 0,
            excluded_next: 0,
            shift_empty_focus_while_risk_positive: false,
        },
        presentation: {},
        focus: null,
        queue: [],
        actions: [],
    };
}

function adminDefaultRevenueLeak() {
    return {
        total_leak_kzt: 0,
        action_risk_kzt: 0,
        recovered_today_kzt: 0,
        focus_completed_today: 0,
        surfaces: [],
        breakdown: {
            abandoned_drafts_kzt: 0,
            slow_response_kzt: 0,
            cancelled_today_kzt: 0,
            menu_confusion_kzt: 0,
        },
    };
}

function adminDefaultMoneyQueue() {
    return {
        summary: {
            total: 0,
            critical: 0,
            abandoned_drafts: 0,
            pending_prepay: 0,
            slow_chats: 0,
        },
        items: [],
    };
}

/** Общие настройки Chart.js (Phase U5): шрифты, сетка. */
function adminChartJsCommonFont() {
    return { family: 'system-ui, -apple-system, Segoe UI, sans-serif', size: 11 };
}

function adminDestroyDashboardChart() {
    if (!charts.dashboard) return;
    try {
        charts.dashboard.destroy();
    } catch (_e) { /* ignore */ }
    charts.dashboard = null;
}

function adminDestroyAnalyticsMainChart() {
    if (!charts.analytics) return;
    try {
        charts.analytics.destroy();
    } catch (_e) { /* ignore */ }
    charts.analytics = null;
}

/** Ссылка на нативный console — не переопределять через adminLogger (избегаем рекурсии). */
const _adminConsole = typeof console !== 'undefined' ? console : { debug() {}, info() {}, warn() {}, error() {} };

/**
 * Централизованный лог клиентской админки: один префикс, управление шумом.
 * Уровень по умолчанию — info (видны info/warn/error, скрыт шум debug). Сообщения `error` всегда выводятся.
 * Параметры: `?admin_log=debug|info|warn|error|silent`, `localStorage.restomind_admin_log=debug`,
 * либо `window.__RESTOMIND_ADMIN_LOG_LEVEL__` = 0…4 (silent…debug).
 */
const ADMIN_LOG_LEVELS = { silent: 0, error: 1, warn: 2, info: 3, debug: 4 };

function resolveAdminLogLevel() {
    try {
        if (typeof window !== 'undefined' && window.__RESTOMIND_ADMIN_LOG_LEVEL__ != null) {
            const n = Number(window.__RESTOMIND_ADMIN_LOG_LEVEL__);
            if (Number.isFinite(n)) return Math.max(0, Math.min(4, n));
        }
        if (typeof window !== 'undefined') {
            const p = new URLSearchParams(window.location.search);
            const q = (p.get('admin_log') || '').toLowerCase();
            if (q === 'silent' || q === '0') return 0;
            if (q === 'error' || q === '1') return 1;
            if (q === 'warn' || q === '2') return 2;
            if (q === 'info' || q === '3') return 3;
            if (q === 'debug' || q === '4') return 4;
            try {
                if (window.localStorage?.getItem('restomind_admin_log') === 'debug') return 4;
            } catch (_e) { /* ignore */ }
        }
    } catch (_e) { /* ignore */ }
    return ADMIN_LOG_LEVELS.info;
}

const adminLogger = {
    _level: resolveAdminLogLevel(),
    setLevel(n) {
        const x = Number(n);
        if (Number.isFinite(x)) this._level = Math.max(0, Math.min(4, x));
    },
    debug(...args) {
        if (this._level >= ADMIN_LOG_LEVELS.debug) _adminConsole.debug('[RestoMind]', ...args);
    },
    info(...args) {
        if (this._level >= ADMIN_LOG_LEVELS.info) _adminConsole.info('[RestoMind]', ...args);
    },
    warn(...args) {
        if (this._level >= ADMIN_LOG_LEVELS.warn) _adminConsole.warn('[RestoMind]', ...args);
    },
    error(...args) {
        _adminConsole.error('[RestoMind]', ...args);
    },
};

if (typeof window !== 'undefined') {
    window.adminLogger = adminLogger;
}

// Глобальная диагностика: иногда вкладка "не грузится" из-за исключения/промиса,
// но пользователь не видит консоль (фильтры/группы/скрытый уровень).
// Эти хендлеры гарантированно выводят ошибку через error-level.
if (typeof window !== 'undefined') {
    try {
        window.addEventListener('error', (ev) => {
            try {
                const msg = ev?.message || 'Unknown error';
                const src = ev?.filename || '';
                const line = ev?.lineno || '';
                const col = ev?.colno || '';
                adminLogger.error('[global] error', `${msg} @ ${src}:${line}:${col}`, ev?.error || ev);
            } catch (_e) {
                adminLogger.error('[global] error', ev);
            }
        });
        window.addEventListener('unhandledrejection', (ev) => {
            try {
                adminLogger.error('[global] unhandledrejection', ev?.reason || ev);
            } catch (_e) {
                adminLogger.error('[global] unhandledrejection');
            }
        });
        // Один "маяк", чтобы точно понять: консоль работает, уровень логов не silent.
        adminLogger.info('[admin] app boot', { logLevel: adminLogger._level });
    } catch (_e) { /* ignore */ }
}

/**
 * Видим ли узел в layout (после Alpine x-show / Tailwind): размер, display, opacity.
 * @param {Element | null | undefined} el
 * @returns {boolean}
 */
function adminIsDomElementVisible(el) {
    if (!el || typeof el.getBoundingClientRect !== 'function') return false;
    try {
        const r = el.getBoundingClientRect();
        if (!Number.isFinite(r.width) || !Number.isFinite(r.height) || r.width < 2 || r.height < 2) {
            return false;
        }
        const st = window.getComputedStyle(el);
        if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
        return true;
    } catch (_e) {
        return false;
    }
}

/**
 * Какой `data-rm-tab-surface` должен быть виден для текущего состояния (регрессии «пустой экран»).
 * @param {{ currentTab: string, dashboardTab: string, settingsTab: string }} ctx
 * @returns {{ key: string, selector: string } | null}
 */
function adminTabSurfaceAuditTarget(ctx) {
    const ct = ctx && ctx.currentTab;
    const dt = ctx && ctx.dashboardTab;
    const st = ctx && ctx.settingsTab;
    if (ct === 'menu') return { key: 'menu', selector: '[data-rm-tab-surface="menu"]' };
    if (ct === 'dashboard' && dt === 'analytics') {
        return { key: 'dashboard:analytics', selector: '[data-rm-tab-surface="dashboard-analytics"]' };
    }
    if (ct === 'settings' && st === 'restaurant') {
        return { key: 'settings:restaurant', selector: '[data-rm-tab-surface="settings-restaurant"]' };
    }
    return null;
}

/** Форматирование вне Alpine — без лишних замыканий в шаблоне; единый символ ₸. */
const adminFormat = {
    /** Число с разделителями, без символа валюты (для подписей Chart.js и сборки строк). */
    moneyAmount(v) {
        const n = Number(v);
        const x = Number.isFinite(n) ? n : 0;
        return x.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
    },
    /**
         * Сумма в тенге. useDash: нечисло → длинное тире (сводка по клиенту в чате).
     */
    money(v, useDash = false) {
        const n = Number(v);
        if (useDash && !Number.isFinite(n)) return '—';
        const x = Number.isFinite(n) ? n : 0;
        return `${x.toLocaleString('ru-RU', { maximumFractionDigits: 0 })} ₸`;
    },
    /**
     * Единая нормализация даты для date/time: Date, YYYY-MM-DD (UTC полдень), ISO со пробелом вместо T.
     */
    _parseDateInput(iso) {
        if (iso == null || iso === '') return null;
        if (iso instanceof Date) {
            return Number.isNaN(iso.getTime()) ? null : iso;
        }
        let s = String(iso).trim();
        if (s.includes(' ') && !s.includes('T')) s = s.replace(' ', 'T');
        if (s.length === 10 && s[4] === '-' && s[7] === '-') {
            const d = new Date(`${s}T12:00:00Z`);
            return Number.isNaN(d.getTime()) ? null : d;
        }
        const d = new Date(s);
        return Number.isNaN(d.getTime()) ? null : d;
    },
    /** ISO или календарная дата YYYY-MM-DD (ось UTC полдень для согласованности с графиками). */
    date(iso) {
        const d = this._parseDateInput(iso);
        if (!d) return '—';
        return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short', year: 'numeric' });
    },
    bookingDate(dateStr) {
        if (dateStr == null || dateStr === '') return '';
        const d = new Date(`${String(dateStr).trim()}T00:00:00`);
        if (Number.isNaN(d.getTime())) return '';
        return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' });
    },
    time(iso) {
        const d = this._parseDateInput(iso);
        if (!d) return '';
        return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    },
    /** Абсолютная метка для title/tooltip. */
    dateTime(iso) {
        const d = this._parseDateInput(iso);
        if (!d) return '';
        return d.toLocaleString('ru-RU', {
            day: '2-digit',
            month: 'short',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    },
    /**
     * Относительное время для лент (чаты, заказы, инциденты).
     * Старше ~7 дней — показываем дату как в `date()`.
     */
    timeAgo(iso) {
        const d = this._parseDateInput(iso);
        if (!d) return '';
        const sec = Math.round((Date.now() - d.getTime()) / 1000);
        if (sec < 0) return this.time(iso);
        if (sec < 45) return 'только что';
        try {
            const rtf = new Intl.RelativeTimeFormat('ru', { numeric: 'auto' });
            if (sec < 3600) return rtf.format(-Math.floor(sec / 60), 'minute');
            if (sec < 86400) return rtf.format(-Math.floor(sec / 3600), 'hour');
            if (sec < 604800) return rtf.format(-Math.floor(sec / 86400), 'day');
        } catch (_e) {
            /* ignore */
        }
        return this.date(iso);
    },
    /** Чистит WhatsApp-разметку для отображения в UI (*bold*, декоративные разделители). */
    chatText(raw) {
        if (!raw) return '';
        return raw
            .replace(/\*([^*]+)\*/g, '$1')
            .replace(/[✨━─═]+\s*/g, '')
            .trim();
    },
};

/**
 * Фрагмент админки в location.hash.
 * Примеры: #dashboard, #chats?phone=7705…, #settings/connections, #inbox?tab=system, #dashboard?tab=analytics.
 * @returns {{ tab: string | null, settingsTab: string | null, phone: string | null, menuView: string | null, inboxTab: string | null, dashboardTab: string | null, aiCenterTab: string | null }}
 */
function adminParseLocationHash() {
    const empty = { tab: null, settingsTab: null, phone: null, menuView: null, inboxTab: null, dashboardTab: null, aiCenterTab: null };
    const raw = String(window.location.hash || '').replace(/^#/, '').trim();
    if (!raw) return { ...empty };
    const q = raw.indexOf('?');
    const path = (q >= 0 ? raw.slice(0, q) : raw).trim();
    const qs = q >= 0 ? raw.slice(q + 1) : '';
    let phone = null;
    let menuView = null;
    let subTab = '';
    try {
        const sp = new URLSearchParams(qs);
        const p = (sp.get('phone') || '').trim();
        phone = p || null;
        const mv = (sp.get('view') || '').trim().toLowerCase();
        if (mv === 'stoplist' || mv === 'catalog') menuView = mv;
        subTab = (sp.get('tab') || '').trim().toLowerCase();
    } catch (_e) {
        phone = null;
    }
    if (path.startsWith('settings/')) {
        const st = path.slice('settings/'.length).trim();
        return { ...empty, tab: 'settings', settingsTab: st || 'restaurant' };
    }
    if (path === 'settings') {
        return { ...empty, tab: 'settings' };
    }
    const legacyToSettings = {
        integrations: 'connections',
        packaging: 'restaurant',
        knowledge: 'restaurant',
        upsell: 'smart_sales',
        team: 'team',
        test: 'bot_test',
    };
    if (legacyToSettings[path]) {
        return { ...empty, tab: 'settings', settingsTab: legacyToSettings[path] };
    }
    /** Legacy #stoplist → меню, вкладка стоп-листа */
    if (path === 'stoplist') {
        return { ...empty, tab: 'menu', menuView: 'stoplist' };
    }
    if (path === 'menu') {
        return {
            ...empty,
            tab: 'menu',
            menuView: menuView || 'catalog',
        };
    }

    /** P1.5.0: старые верхние вкладки → inbox / dashboard / ai_center */
    const legacyTop = {
        operator_queue: { tab: 'inbox', inboxTab: 'clients' },
        errors: { tab: 'inbox', inboxTab: 'clients' },
        incidents: { tab: 'inbox', inboxTab: 'system' },
        analytics: { tab: 'dashboard', dashboardTab: 'analytics' },
        ai_value: { tab: 'ai_center', aiCenterTab: 'value' },
        intelligence: { tab: 'ai_center', aiCenterTab: 'insights' },
        digital_twin: { tab: 'ai_center', aiCenterTab: 'load' },
    };
    if (legacyTop[path]) {
        const L = legacyTop[path];
        return {
            ...empty,
            tab: L.tab,
            phone,
            inboxTab: L.inboxTab ?? null,
            dashboardTab: L.dashboardTab ?? null,
            aiCenterTab: L.aiCenterTab ?? null,
        };
    }

    if (path === 'inbox') {
        const it = subTab === 'system' ? 'system' : 'clients';
        return { ...empty, tab: 'inbox', phone, inboxTab: it };
    }
    if (path === 'dashboard') {
        const dt = subTab === 'analytics' ? 'analytics' : 'overview';
        return { ...empty, tab: 'dashboard', phone, dashboardTab: dt };
    }
    if (path === 'ai_center') {
        let ac = 'value';
        if (subTab === 'insights') ac = 'insights';
        else if (subTab === 'load') ac = 'load';
        else if (subTab === 'os') ac = 'os';
        else if (subTab === 'guestcare') ac = 'guestcare';
        else if (subTab === 'final_mile') ac = 'final_mile';
        else if (subTab === 'value') ac = 'value';
        return { ...empty, tab: 'ai_center', phone, aiCenterTab: ac };
    }

    return { ...empty, tab: path || null, phone };
}

/** Допустимые верхнеуровневые вкладки (id из navItems). */
const ADMIN_TOP_TAB_IDS = new Set([
    'shift', 'dashboard', 'inbox', 'ai_center', 'marketing', 'orders', 'bookings', 'chats', 'menu', 'settings',
]);

/**
 * Focus-Driven OS — Mode Engine (Sprint 1, Strangler).
 * Matrix: docs/UI_MAP.md § Focus-Driven Admin Shell, docs/UI_DESIGN_SYSTEM.md § Focus-Driven OS.
 */
const ADMIN_MODE_STORAGE_KEY = 'restomind_admin_mode';

const ADMIN_MODES = Object.freeze(['shift', 'control', 'intelligence']);

const ADMIN_MODE_DEFAULT_TAB = Object.freeze({
    shift: 'shift',
    control: 'inbox',
    intelligence: 'dashboard',
});

/** nav id `shift` → `_tab_shift_control.html`. */
const ADMIN_MODE_TABS = Object.freeze({
    shift: Object.freeze(['shift']),
    control: Object.freeze(['inbox', 'orders', 'chats', 'bookings', 'menu']),
    intelligence: Object.freeze(['dashboard', 'ai_center', 'settings', 'marketing']),
});

const ADMIN_VALID_MODES = new Set(ADMIN_MODES);

function adminNormalizeAdminMode(mode) {
    const m = String(mode || '').trim().toLowerCase();
    return ADMIN_VALID_MODES.has(m) ? m : null;
}

function adminModeForTab(tabId) {
    const id = String(tabId || '').trim();
    if (!id) return null;
    for (const mode of ADMIN_MODES) {
        if (ADMIN_MODE_TABS[mode].includes(id)) return mode;
    }
    return null;
}

function adminTabBelongsToMode(tabId, mode) {
    const m = adminNormalizeAdminMode(mode);
    if (!m) return false;
    return ADMIN_MODE_TABS[m].includes(String(tabId || '').trim());
}

function adminDefaultTabForMode(mode) {
    const m = adminNormalizeAdminMode(mode);
    return m ? (ADMIN_MODE_DEFAULT_TAB[m] || ADMIN_MODE_DEFAULT_TAB.control) : ADMIN_MODE_DEFAULT_TAB.control;
}

function adminTabsForMode(mode) {
    const m = adminNormalizeAdminMode(mode);
    return m ? [...ADMIN_MODE_TABS[m]] : [...ADMIN_MODE_TABS.control];
}

/** Role-first IA (Sprint 5 pivot): sidebar visibility by staff role. */
const ADMIN_ANALYTICS_DENSITY_STORAGE_KEY = 'restomind_analytics_density';
const ADMIN_OPERATIONS_DENSITY_STORAGE_KEY = 'restomind_density:operations';
const ADMIN_UI_HINTS_STORAGE_KEY = 'restomind_ui_hints_v1';
const ADMIN_OPERATIONS_TABS = Object.freeze(['shift', 'inbox', 'orders', 'chats', 'bookings']);

function adminReadUiHintsDismissed() {
    try {
        const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(ADMIN_UI_HINTS_STORAGE_KEY) : null;
        const parsed = raw ? JSON.parse(raw) : {};
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_e) {
        return {};
    }
}

const ADMIN_ROLE_TABS = Object.freeze({
    operator: Object.freeze(['shift', 'inbox', 'orders', 'chats', 'bookings']),
    manager: Object.freeze(['shift', 'inbox', 'orders', 'chats', 'bookings', 'menu', 'dashboard', 'ai_center']),
    admin: null,
});

/** Wow Layer: primary sidebar/bottom nav for operator (secondary via «Ещё»). */
const ADMIN_ROLE_PRIMARY_NAV = Object.freeze({
    operator: Object.freeze(['shift', 'inbox']),
    manager: null,
    admin: null,
});

const ADMIN_OPERATOR_SECONDARY_TABS = Object.freeze(['orders', 'chats', 'bookings']);

/** G10.6 golden UX flow — complete/skip choreography (ms). */
const SHIFT_CHOREO_MS = Object.freeze({
    pauseBeforeExit: 150,
    exitDuration: 200,
    impactRevealDelay: 200,
    impactPrefixReveal: 120,
    impactEmotionReveal: 180,
    impactMoneyReveal: 100,
    pulseAfterImpact: 300,
    focusEnterAfterPulse: 500,
});

function adminSleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
}

function adminLiveImpactUsesCompressed(liveImpact) {
    return !!(liveImpact && (liveImpact.narrative_compressed || liveImpact.outcome_emotion));
}

function adminLiveImpactOutcomePrefix(liveImpact) {
    if (!liveImpact) return '';
    if (liveImpact.outcome_prefix) return String(liveImpact.outcome_prefix).trim();
    const action = String(liveImpact.last_action || '');
    if (action === 'focus_completed') return 'Риск был высок…';
    return '';
}

function adminLiveImpactOutcomeEmotion(liveImpact) {
    if (!liveImpact) return '';
    if (liveImpact.outcome_emotion) return String(liveImpact.outcome_emotion).trim();
    const action = String(liveImpact.last_action || '');
    if (action === 'focus_skipped') return 'Отложили — следующая задача';
    const reason = String(liveImpact.impact_reason || '').trim();
    if (reason.includes('Клиент')) return 'Вернули клиента';
    return reason || 'Готово';
}

function adminLiveImpactMoneyShort(liveImpact) {
    if (!liveImpact) return '';
    if (liveImpact.impact_money) return String(liveImpact.impact_money).trim();
    const text = String(liveImpact.impact_text || '').trim();
    const amount = Number(liveImpact.amount_kzt ?? 0);
    if (amount > 0 && text) {
        const m = text.match(/\+[\d\s]+₸/);
        if (m) return m[0].trim();
        return text;
    }
    if (text.startsWith('+')) return text.split(' ').slice(0, 2).join(' ');
    return '';
}

/** Legacy combined line (fallback when not compressed). */
function adminRenderLiveImpactNarrative(liveImpact) {
    if (!liveImpact || typeof liveImpact !== 'object') return '';
    if (adminLiveImpactUsesCompressed(liveImpact)) {
        return adminLiveImpactOutcomeEmotion(liveImpact);
    }
    const reason = String(liveImpact.impact_reason || '').trim();
    const text = String(liveImpact.impact_text || '').trim();
    const action = String(liveImpact.last_action || '');
    if (action === 'focus_skipped') {
        return reason || text || 'Отложено — следующая задача';
    }
    if (reason && text) return `${reason} → ${text}`;
    return text || reason;
}

function adminLiveImpactMoneyLabel(liveImpact) {
    return adminLiveImpactMoneyShort(liveImpact);
}

function adminLiveImpactReasonOnly(liveImpact) {
    if (!liveImpact || adminLiveImpactUsesCompressed(liveImpact)) return '';
    const narrative = adminRenderLiveImpactNarrative(liveImpact);
    const money = adminLiveImpactMoneyLabel(liveImpact);
    const reason = String(liveImpact.impact_reason || '').trim();
    if (narrative.includes('→') && money) return '';
    return reason;
}

function adminNormalizeStaffRole(role) {
    const r = String(role || 'admin').trim().toLowerCase();
    if (r === 'operator' || r === 'manager' || r === 'admin') return r;
    return 'admin';
}

function adminTabsForRole(role) {
    const r = adminNormalizeStaffRole(role);
    const tabs = ADMIN_ROLE_TABS[r];
    if (tabs === null) return null;
    return [...tabs];
}

function adminTabVisibleForRole(tabId, role) {
    const tabs = adminTabsForRole(role);
    if (tabs === null) return true;
    return tabs.includes(String(tabId || '').trim());
}

function adminTabPrimaryNavForRole(tabId, role) {
    const r = adminNormalizeStaffRole(role);
    const primary = ADMIN_ROLE_PRIMARY_NAV[r];
    if (primary === null) return adminTabVisibleForRole(tabId, role);
    return primary.includes(String(tabId || '').trim());
}

function adminOperatorSecondaryTab(tabId) {
    return ADMIN_OPERATOR_SECONDARY_TABS.includes(String(tabId || '').trim());
}

/** Sprint B: чаты грузим только на вкладках, где список реально нужен. */
function adminTabNeedsChatList(tabId) {
    const t = String(tabId || '').trim();
    return t === 'chats' || t === 'inbox';
}

function adminTabNeedsRevenueLeak(tabId) {
    const t = String(tabId || '').trim();
    return t === 'dashboard' || t === 'shift';
}

function adminTabNeedsShiftState(tabId) {
    const t = String(tabId || '').trim();
    return t === 'dashboard' || t === 'shift';
}

function adminResolveOperatorLandingTab(shiftState) {
    const ss = shiftState && typeof shiftState === 'object' ? shiftState : {};
    const risk = Number(ss.metrics?.risk_kzt ?? 0);
    if (risk > 0) return 'shift';
    if (ss.focus?.id) return 'shift';
    return 'inbox';
}

function adminDefaultTabForRole(role) {
    const r = adminNormalizeStaffRole(role);
    if (r === 'operator') return 'inbox';
    return 'dashboard';
}

/** Focus-Driven OS — Command Bar prefixes (Sprint 4). */
const ADMIN_COMMAND_DEFINITIONS = Object.freeze([
    Object.freeze({
        id: 'leak',
        prefix: '/leak',
        label: 'Упущенная выручка',
        hint: 'Режим Intelligence → дашборд с revenue leak',
        mode: 'intelligence',
    }),
    Object.freeze({
        id: 'red',
        prefix: '/red',
        label: 'Риск · смена',
        hint: 'Режим Shift → контроль смены',
        mode: 'shift',
    }),
    Object.freeze({
        id: 'force-close',
        prefix: '/force-close',
        label: 'Экстренное закрытие',
        hint: 'Профиль заведения → закрыть на N минут',
        mode: null,
    }),
]);

/**
 * Parse command-bar query for known `/prefix` commands.
 * @param {string} query
 * @returns {{ id: string, prefix: string, args: string } | null}
 */
function adminParseCommand(query) {
    const raw = String(query || '').trim();
    if (!raw.startsWith('/')) return null;
    const lower = raw.toLowerCase();
    for (const def of ADMIN_COMMAND_DEFINITIONS) {
        const p = def.prefix.toLowerCase();
        if (lower === p || lower.startsWith(`${p} `)) {
            return {
                id: def.id,
                prefix: def.prefix,
                args: raw.slice(def.prefix.length).trim(),
            };
        }
    }
    return null;
}

/** Filter command definitions matching partial `/` query. */
function adminCommandBarSuggestions(query) {
    const raw = String(query || '').trim().toLowerCase();
    if (!raw.startsWith('/')) return [];
    if (raw === '/') return [...ADMIN_COMMAND_DEFINITIONS];
    return ADMIN_COMMAND_DEFINITIONS.filter((def) => def.prefix.toLowerCase().startsWith(raw));
}

if (typeof window !== 'undefined') {
    window.adminCommandBar = {
        DEFINITIONS: ADMIN_COMMAND_DEFINITIONS,
        parseCommand: adminParseCommand,
        suggestions: adminCommandBarSuggestions,
    };
    window.adminModeEngine = {
        MODES: ADMIN_MODES,
        STORAGE_KEY: ADMIN_MODE_STORAGE_KEY,
        modeForTab: adminModeForTab,
        tabsForMode: adminTabsForMode,
        defaultTabForMode: adminDefaultTabForMode,
        tabBelongsToMode: adminTabBelongsToMode,
        normalizeMode: adminNormalizeAdminMode,
    };
    window.adminRoleNav = {
        ROLE_TABS: ADMIN_ROLE_TABS,
        tabsForRole: adminTabsForRole,
        tabVisibleForRole: adminTabVisibleForRole,
        resolveOperatorLandingTab: adminResolveOperatorLandingTab,
        defaultTabForRole: adminDefaultTabForRole,
        normalizeStaffRole: adminNormalizeStaffRole,
        ANALYTICS_DENSITY_STORAGE_KEY: ADMIN_ANALYTICS_DENSITY_STORAGE_KEY,
    };
}

/** Начальное состояние GET /integrations/status — чтобы Alpine не падал на undefined до первой загрузки. */
function defaultIntegrationStatus() {
    return {
        iiko_configured: false,
        whatsapp_configured: false,
        telegram_configured: false,
        openai_configured: false,
        whatsapp_voice_replies_enabled: false,
        webhook_url: '',
        whatsapp_verify_token_hint: '',
        last_stoplist: { at: null, ok: false, error: null },
        last_menu_sync: { at: null, ok: false, error: null },
        iiko_secrets_encrypt_ready: false,
        prepayment_enforced: true,
        auto_send_to_iiko_after_payment: false,
        payment_providers: {
            freedom_pay: { enabled: false, secret_configured: false },
            kaspi: { enabled: false, secret_configured: false },
            cloudpayments: { enabled: false, secret_configured: false },
        },
    };
}

/** Базовая форма ручного черновика (модалка «+ Новый заказ (тест)»). */
function defaultManualOrderForm() {
    return {
        phone: '+77001234567',
        order_type: 'pickup',
        payment_mode: 'single',
        payment_method: 'cash',
        split_cash: 0,
        split_card: 0,
        split_remote: 0,
        delivery_address: '',
        pickup_time_note: '',
    };
}

/** Полная форма создания правила упаковки (чтобы не заполнять поля постфактум). */
function defaultPackagingRuleForm() {
    return {
        kind: '',
        name: '',
        price: 0,
        keywords: '',
        option_key: '',
        scope: 'item',
        category_match: '',
        iiko_product_id: '',
        sort_order: 0,
        is_active: true,
    };
}

/** Поля состояния (вкладки, сущности, UI) */
function adminMixinState() {
    return {
        // Кэш сортировки заказов (чтобы не фризить UI на каждом reactive-триггере)
        _ordersSortedSig: '',
        _ordersSortedCache: [],

        // Авторизация
        authenticated: false,
        loginUsername: '',
        loginPassword: '',
        loginError: '',
        loginLoading: false,
        /** Показали ли уже модалку «сессия истекла» для серии 401 (не хранить на window). */
        auth401AlertShown: false,
        wsToken: '',
        /** Роль staff из API: admin | operator (для стартовой вкладки). */
        staffRole: '',
        isSuperadmin: false,
        _adminHashWatchInstalled: false,
        _applyingHashFromBrowser: false,
        _hashPushTimer: null,
        hasDemoData: false,
        demoActionLoading: false,
        showDemoDeleteModal: false,
        demoDeleteAck: false,
        demoDeleteError: '',
        demoToastMessage: '',
        /** Вариант стиля нижнего тоста: success | warning | error | info */
        demoToastKind: 'info',
        _demoToastTimer: null,

        /** Универсальная модалка вместо window.confirm / window.prompt */
        uiConfirmOpen: false,
        uiConfirmTitle: 'Подтверждение',
        uiConfirmMessage: '',
        uiConfirmDanger: false,
        uiConfirmConfirmText: 'Подтвердить',
        uiConfirmCancelText: 'Отмена',
        uiConfirmShowCancel: true,
        uiConfirmShowInput: false,
        uiConfirmInputReadonly: false,
        uiConfirmInputLabel: '',
        uiConfirmInputPlaceholder: '',
        uiConfirmInputValue: '',
        uiConfirmInputRequired: false,
        uiConfirmError: '',
        uiConfirmSubmitting: false,
        /** Многострочное поле в uiConfirm (JSON и длинный текст). */
        uiConfirmInputMultiline: false,
        uiConfirmInputRows: 6,
        /** Чекбокс «Я понимаю…» для деструктивных действий: primary кнопка disabled пока false. */
        uiConfirmRequireAck: false,
        uiConfirmAckLabel: '',
        uiConfirmAckChecked: false,
        /** Кнопка «Форматировать JSON» в модалке (многострочный ввод). */
        uiConfirmShowFormatJson: false,

        /** Вкладка «Стоп-лист»: только позиции с is_available=false */
        stopListItems: [],
        stopListFilteredItems: [],
        stopListLoadError: '',
        stopListSearchQuery: '',
        stopListSyncLoading: false,

        /** Вкладка «Настройки»: список заказов для выборочного удаления */
        settingsOrdersList: [],
        settingsOrdersLoading: false,
        settingsSelectedOrderIds: [],
        settingsMenuClearLoading: false,
        settingsMenuStopClearLoading: false,
        settingsPurgeModalOpen: false,
        settingsPurgePhrase: '',
        settingsPurgeAck: false,
        settingsPurgeLoading: false,
        settingsPurgeError: '',
        settingsEnv: adminDefaultSettingsEnv(),
        settingsEnvLoading: false,
        settingsRedisPhone: '',
        settingsRedisPurgeLoading: false,
        settingsExportDateFrom: '',
        settingsExportDateTo: '',
        settingsExportLoading: false,
        settingsBulkCancelLoading: false,
        settingsRetentionRunLoading: false,

        // Команда
        teamUsers: [],
        teamLoading: false,
        teamCreateLoading: false,
        teamError: '',
        teamNewEmail: '',
        teamNewRole: 'operator',
        teamNewPassword: '',
        teamNewMetaTitle: '',
        teamNewMetaDepartment: '',
        teamNewLocationIds: [],
        teamEditId: null,
        teamEditRole: 'operator',
        teamEditMetaTitle: '',
        teamEditMetaDepartment: '',
        teamEditLocationIds: [],
        teamEditSaving: false,
        teamTempPassword: '',
        staffMindSessions: [],
        staffMindLoading: false,
        staffMindStartLoading: false,
        staffMindAskLoadingId: null,
        staffMindPhone: '',
        staffMindRole: 'staff',
        staffMindQuestionById: {},

        packagingRules: [],
        packagingFilter: 'all',
        packagingLoading: false,
        packagingCreateOpen: false,
        packagingCreateLoading: false,
        packagingCreateError: '',
        packagingCreateForm: defaultPackagingRuleForm(),

        /** Филиал: автоматическая предоплата по порогу (см. PATCH /organization/prefs). */
        orgPrepaymentEnforcedSaving: false,
        orgAutoIikoSaving: false,
        paymentProviderSaving: null,

        knowledgeItems: [],
        knowledgeLoading: false,
        knowledgeSaveLoading: false,
        knowledgeEditOpen: false,
        knowledgeEditError: '',
        knowledgeEditForm: {
            id: null,
            category: '',
            knowledge_kind: 'facility',
            question: '',
            answer: '',
            is_active: true,
            sort_order: 0,
        },

        currentTab: 'dashboard',
        /** P0 lazy DOM: монтировать разметку тяжёлых вкладок после первого визита (`admin.html` + `template x-if`). */
        lazyTabMount: { chats: false, orders: false, bookings: false, settings: false },
        /** E5 UI: ответ `GET /api/admin/system/task-queue-health` (если есть на бэкенде). */
        taskQueueHealth: null,
        taskQueueHealthChecked: false,
        /** Каталог vs стоп-лист внутри вкладки «Меню» (Phase U5). */
        menuView: 'catalog',
        /** Загрузка данных при смене вкладки (избегаем общего имени `loading` — конфликт миксинов). */
        tabDataLoading: false,
        /** Кэш сортировки таблицы «по дням» на аналитике (геттер не пересортировывает на каждый тик Alpine). */
        _analyticsDailySig: '',
        _analyticsDailySortedCache: [],
        /** Инкремент при loadAnalytics — сигнатура сортировки без тяжёлого map/join по всем дням. */
        analyticsDailyDataRev: 0,
        /** Заголовки секций сайдбара (один x-for в шаблоне). */
        navSections: [
            { id: 'operations', title: 'Операции' },
            { id: 'management', title: 'Управление' },
        ],
        /** Под-табы «Требует внимания»: клиенты vs системные инциденты (P1.5.0). */
        inboxTab: 'clients',
        /** Под-табы дашборда: главная vs аналитика (P1.5.0). */
        dashboardTab: 'overview',
        /** Role-first IA: normal (hero KPI) vs advanced (full analytics). */
        analyticsDensity: 'normal',
        /** Под-табы ИИ-центра: вклад / инсайты / нагрузка (P1.5.0). */
        aiCenterTab: 'value',
        /** Вкладка внутри Settings (Stripe-like). */
        settingsTab: 'restaurant', // restaurant | branding | connections | smart_sales | team | …
        orgProfileDirty: false,
        brandingDirty: false,
        navItems: [
            { id: 'shift', section: 'operations', label: 'Смена', desc: 'Управление деньгами и приоритетами смены',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>' },
            { id: 'inbox', section: 'operations', label: 'Требует внимания', desc: 'Очередь помощи и системные инциденты',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/></svg>' },
            { id: 'orders', section: 'operations', label: 'Заказы', desc: 'По этапам (черновик → подтверждён → кухня) или общий список',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z"/></svg>' },
            { id: 'chats', section: 'operations', label: 'Диалоги', desc: 'Сообщения и ответы клиентам',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a21.05 21.05 0 00-1.889-2.403 19.7 19.7 0 00-1.6-1.562c-.642-.522-1.397-.957-2.23-1.25C16.247 1.872 14.747 1.5 12 1.5c-2.747 0-4.247.372-5.63.99-.833.293-1.588.728-2.23 1.25-.563.459-1.082 1-1.6 1.562A21.05 21.05 0 003.75 8.511"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M5.25 8.511c-.884.284-1.5 1.128-1.5 2.097v4.286c0 1.136.847 2.1 1.98 2.193.34.027.68.052 1.02.072v3.091l3-3a11.63 11.63 0 014.02-.163 2.115 2.115 0 001.825-.242M9.378 5.378A21.05 21.05 0 0018.72 3.728"/></svg>' },
            { id: 'bookings', section: 'operations', label: 'Бронирования', desc: 'Столики и резервации',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5"/></svg>' },
            { id: 'dashboard', section: 'management', label: 'Дашборд', desc: 'Общая статистика и последние заказы',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25A2.25 2.25 0 018.25 10.5H6A2.25 2.25 0 013.75 8.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z"/></svg>' },
            { id: 'marketing', section: 'management', label: 'Маркетинг', desc: 'Рассылки и программа лояльности',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10.34 15.84c-.688-.06-1.386-.09-2.09-.09H7.5a4.5 4.5 0 110-9h.75c.704 0 1.402-.03 2.09-.09m0 9.18c.253.962.584 1.892.985 2.783.247.55.06 1.21-.463 1.511l-.657.38c-.551.318-1.26.117-1.527-.461a20.845 20.845 0 01-1.44-4.282m3.102.069a18.03 18.03 0 01-.59-4.59c0-1.586.205-3.124.59-4.59m0 9.18a23.848 23.848 0 018.835 2.535M10.34 6.66a23.847 23.847 0 008.835-2.535m0 0A23.74 23.74 0 0018.795 3m.38 1.125a23.91 23.91 0 011.014 5.395m-1.014 8.855c-.118.38-.245.754-.38 1.125m.38-1.125a23.91 23.91 0 001.014-5.395m0-3.46c.495.413.811 1.035.811 1.73 0 .695-.316 1.317-.811 1.73m0-3.46a24.347 24.347 0 010 3.46"/></svg>' },
            { id: 'ai_center', section: 'management', label: 'ИИ-аналитика', desc: 'Вклад ИИ, инсайты и нагрузка',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.75 3.75h4.5m-7.5 4.5h10.5m-12 4.5h13.5m-15 4.5h7.5m2.25 0h6M7.5 21h9a2.25 2.25 0 002.25-2.25V5.25A2.25 2.25 0 0016.5 3h-9a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21z"/></svg>' },
            { id: 'menu', section: 'management', label: 'Меню', desc: 'Каталог и стоп-лист',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8.25v-1.5m0 1.5c-1.355 0-2.697.056-4.024.166C6.845 8.51 6 9.473 6 10.608v2.513m6-4.871c1.355 0 2.697.055 4.024.165C17.155 8.51 18 9.473 18 10.608v2.513m-3 4.73v-1.59c0-.532-.21-1.042-.586-1.418L12 13.5m-3 4.73c.55.47 1.27.73 2 .73h6c.73 0 1.45-.26 2-.73m-8-4.73V10.6c0-1.12.856-2.08 2.09-2.19.64-.09 1.29-.14 1.91-.14m5 6.37v1.59c0 1.632-.875 3.11-2.25 3.89"/></svg>' },
            { id: 'settings', section: 'management', label: 'Настройки', desc: 'Ресторан, подключения, продажи, команда',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>' },
        ],

        // WebSocket (статус в сайдбаре: readyState + ws_ready с сервера)
        ws: null,
        /** Канал событий реально подписан (после кадра ws_ready от бэкенда). */
        wsChannelReady: false,
        _wsReadyTimer: null,
        /** Счётчик для перерисовки индикатора (readyState WebSocket не реактивен в Alpine). */
        wsEpoch: 0,
        wsReconnectDelay: 1000,

        // Алерты
        alertQueue: [],

        /** Последние события из WebSocket (дашборд). */
        dashLiveFeed: [],
        /** Activity Feed (с сервера) — красиво, не “лог”. */
        dashActivity: [],
        dashActivityLoading: false,

        // Дашборд
        dashStats: {},
        dashStatsLoading: false,
        dashStatsLoadedOnce: false,
        dashFunnel: null,
        dashFunnelLoading: false,
        _dashboardChartObserver: null,
        /** GET /api/admin/roi/today — нарратив + достижения. */
        dashRoiSummary: null,
        dashRoiLoading: false,
        /** Контекст текущего заведения (multi-tenant брендинг в шапке). */
        orgProfile: {
            id: null,
            organization_id: null,
            name: '',
            timezone: '',
            currency: '',
            whatsapp_phone_number_id: '',
            telegram_ops_chat_id: '',
            prepayment_legal_text: '',
            review_url_2gis: '',
            review_url_google: '',
            schedule_json: null,
            schedule_json_text: '',
            operational_label: '',
            is_business_open: false,
            is_kitchen_open: false,
        },
        scheduleEditorOpen: false,
        /** Платёжные конфиги: { freedom_pay: {...}, kaspi: {...}, ... } */
        paymentConfigs: {},
        paymentConfigsLoaded: false,
        scheduleEditorFallbackUsed: false,
        scheduleEditor: {},
        scheduleDayRows: [
            { key: 'mon', label: 'Понедельник' },
            { key: 'tue', label: 'Вторник' },
            { key: 'wed', label: 'Среда' },
            { key: 'thu', label: 'Четверг' },
            { key: 'fri', label: 'Пятница' },
            { key: 'sat', label: 'Суббота' },
            { key: 'sun', label: 'Воскресенье' },
        ],
        /** Данные текущего пользователя (доступные филиалы, брендинг). */
        userData: {
            id: null,
            email: '',
            role: 'operator',
            is_superadmin: false,
            tenant_owner_id: null,
            active_organization_id: null,
            available_organizations: [],
            available_locations: [],
            tenant: null,
            branding: null,
            is_network: false,
            network_orgs: [],
        },
        selectedLocationId: '',
        /** Черновик E2.2.F — синхронизируется с `userData.branding` из `/auth/me`; сохранение через PATCH когда есть E2.2.B. */
        brandingDraft: { brand_name: '', brand_color_hex: '#2563eb' },
        brandingSaving: false,
        brandingLogoPending: null,
        brandingLogoPendingLabel: '',
        /** Локальный preview выбранного файла (revoke при смене). */
        brandingPreviewObjectUrl: '',
        /** True после 404 на PATCH — показываем подсказку до появления API. */
        brandingApiUnavailable: false,
        orgProfileLoading: false,
        _apiEtagCache: {},
        /** Гасим хром при смене филиала, пока не подтянутся профиль и данные вкладок. */
        orgSwitchChromeDimmed: false,
        orgProfileSaving: false,
        forceCloseOpen: false,
        forceCloseSaving: false,
        forceCloseMinutes: 60,
        forceCloseReason: '',
        orders: [],
        _ordersLoadSeq: 0,
        ordersLoading: false,
        ordersLoadError: '',
        ordersPage: 1,
        ordersSize: 50,
        ordersPages: 1,
        ordersTotal: 0,
        ordersHasMore: false,
        bookings: [],
        bookingStatusFilter: 'all',
        bookingWeekAnchor: '',
        bookingSelectedDate: '',
        bookingsLoading: false,
        bookingHallOptions: [
            { key: 'hall_1', label: 'Зал 1' },
            { key: 'hall_2', label: 'Зал 2' },
            { key: 'vip', label: 'VIP зал' },
        ],
        menuItems: [],

        // Заказы
        ordersView: 'kanban',
        _ordersViewAutoSet: false,
        kanbanLateStagesOpen: false,
        kanbanDensity: 'normal',
        operationsDensity: 'normal',
        /** Подсказка режима заказов (канбан / таблица), скрывается через localStorage */
        ordersKanbanHintDismissed:
            typeof localStorage !== 'undefined' &&
            localStorage.getItem('rm_orders_view_hint_v1') === '1',
        uiHintsDismissed: adminReadUiHintsDismissed(),
        customerNoteSavedFlash: false,
        _customerNoteSavedTimer: null,
        menuEmbeddingsReindexLoading: false,
        orderFilter: '',
        /** Поиск и фильтр суммы (список заказов) */
        orderSearchQ: '',
        orderSumMin: '',
        orderSumMax: '',
        ordersMobileFiltersOpen: false,
        /** Сортировка таблицы заказов: id | client | items | total | status | date */
        ordersSort: { column: 'date', dir: 'desc' },
        showOrderModal: false,
        selectedOrder: null,
        /** После drag-and-drop канбана: коротко блокировать открытие карточки по «хвостовому» клику */
        _kanbanBlockOpenUntil: 0,

        failedTasks: [],
        failedTasksTotal: 0,
        failedTasksFilter: 'open',
        failedTasksPhone: '',
        failedTasksLoading: false,
        failedTaskRetryingId: null,
        aiValuePeriod: '7d',
        aiValueCustom: false,
        aiValueFrom: '',
        aiValueTo: '',
        aiValueLoading: false,
        aiValueData: null,
        aiValueSource: '',
        /** Money MVP: Revenue Leak Detector */
        revenueLeak: adminDefaultRevenueLeak(),
        revenueLeakLoading: false,
        revenueLeakActionLoading: '',
        /** KPI офiciантов из iiko (P3 Growth) */
        waiterKpi: { items: [], hall_connected: false, delivery_connected: false, last_sync: null },
        waiterKpiDays: 7,
        waiterKpiLoading: false,
        waiterKpiSyncLoading: false,
        shiftState: adminDefaultShiftState(),
        shiftStateLoading: false,
        shiftStateFetchedAt: 0,
        shiftStateDegraded: false,
        shiftStateLoadError: '',
        shiftActionLoading: '',
        shiftLiveImpactPulse: '',
        shiftFocusEnterKey: 0,
        ownerImpactPulse: false,
        _shiftLiveImpactTimer: null,
        /** G10.6 choreography: idle | exiting | impact | entering */
        shiftChoreoPhase: 'idle',
        /** card | impact | focus — attention steering */
        shiftAttentionTarget: '',
        shiftChoreoImpact: null,
        shiftFocusCardVisible: true,
        /** impact reveal: idle | prefix | emotion | money */
        shiftImpactRevealPhase: 'idle',
        _shiftPreAttentionTimer: null,
        _shiftPreAttentionTick: 0,
        _shiftStateRefreshTimer: null,
        _shiftHeartbeatTimer: null,
        botSlaStatus: { bot_short_mode: false, slow_chats: 0 },
        /** Phase 3b OS: AI Snapshot list */
        aiSnapshots: [],
        aiSnapshotsLoading: false,
        /** Phase 5 OS: OS Autopilot dashboard data from /intelligence/os-dashboard */
        osDashboardData: null,
        osDashboardLoading: false,
        /** Phase 5 Final Mile UI */
        dailyDigestPreview: null,
        dailyDigestLoading: false,
        supplyMindDrafts: [],
        supplyMindAlerts: [],
        supplyMindLoading: false,
        supplyMindCreateLoading: false,
        supplyMindUpdateLoading: null,
        supplyMindExportLoading: null,
        supplyMindCoverDays: 7,
        inventorySyncStatus: null,
        inventorySyncLoading: false,
        inventorySyncRunning: false,
        voiceAiStatus: null,
        voiceAiLoading: false,
        voiceAiSaving: false,
        voiceAiEnabledDraft: false,
        voiceAiModeDraft: 'stt_fallback',
        voiceCallLogs: [],
        voiceCallLogsLoading: false,
        voiceCallLogsUnavailable: false,
        voiceCallLogsTotal: 0,
        voiceCallLogsOffset: 0,
        voiceCallLogsLimit: 15,
        voiceCallLogsHasMore: false,
        supplyMindExpandedDraftId: null,
        supplyMindItemPatchLoading: null,
        /** Control Plane: trace timeline panel (OS tab) */
        traceTimeline: null,
        traceTimelineLoading: false,
        traceTimelineQuery: '',
        /** Phase 5 OS: Audit log feed (OS Decision Feed) */
        auditLog: [],
        auditLogLoading: false,
        auditLogDetail: null,
        guestCareReviews: [],
        guestCareLoading: false,
        guestCareSyncLoading: false,
        guestCareSyncMeta: null,
        guestCareSyncMessage: '',
        guestCareImportUrl: '',
        applyPricingBulkLoading: false,
        intelligenceLoading: false,
        intelligenceAsking: false,
        intelligenceQuestion: '',
        intelligenceAnswer: '',
        intelligenceConversationId: null,
        intelligenceData: { summary: null, insights: [], snapshot: null },
        opEfficiencyData: null,
        opEfficiencyLoading: false,
        latencyData: null,
        latencyLoading: false,
        latencyExpanded: false,
        intelligenceQuickQuestions: [
            'Почему сегодня меньше заказов?',
            'Почему упала выручка?',
            'Что с отменами сегодня?',
            'Сравни сегодня со вчера',
        ],
        digitalTwinLoading: false,
        digitalTwin: { snapshot: {} },
        digitalTwinSim: { orders_per_hour: 30, operators: 2, avg_check: 5000, base_cancel_rate_pct: 5 },
        digitalTwinSimLoading: false,
        digitalTwinSimResult: null,
        incidents: {
            groups: [],
            summary: { critical: 0, warning: 0, info: 0, restricted: 0 },
            total_open: 0,
            severity: 'ok',
            restricted_count: 0,
            generated_at: null,
            is_superadmin: false,
        },
        incidentsLoading: false,
        incidentsLoadedOnce: false,
        /** GET /incidents?mode=summary — блок «Сейчас» на дашборде и счётчик в сайдбаре без тяжёлых групп */
        attentionSummary: null,
        attentionSummaryLoading: false,
        moneyQueue: adminDefaultMoneyQueue(),
        moneyQueueLoading: false,
        /** Время последнего успешного GET /incidents?mode=summary (кэш ~45 с на дашборде) */
        attentionSummaryFetchedAt: 0,
        /** Скрыть системный баннер до следующей загрузки страницы */
        systemBannerDismissed: false,
        orderTimeline: [],
        orderTimelineLoading: false,
        orderTimelineExpanded: false,
        readinessPayload: adminDefaultReadinessPayload(),
        readinessLoading: false,
        /** Сессия demo-login — read-only для мутаций на бэке; для UI дизейбла кнопок демо */
        isDemoSession: false,
        setupProgressExpanded: false,
        setupChecklistOpen: false,
        orderRebuildDraftJson: '',
        orderRebuildError: '',
        orderRebuildLoading: false,
        orderCompositionOpen: false,
        orderEditLines: [],
        orderPayMode: 'single',
        orderPayMethod: 'cash',
        orderPaySplitCash: 0,
        orderPaySplitCard: 0,
        orderPaySplitRemote: 0,
        orderPaymentError: '',
        orderPaymentSaving: false,
        showManualOrderModal: false,
        manualOrderLoading: false,
        manualOrderError: '',
        manualOrderForm: defaultManualOrderForm(),

        showBookingModal: false,
        selectedBooking: null,

        // Аналитика
        /** Сортировка блока «Разбивка по дням»: date | orders | revenue */
        analyticsDaySort: 'date',
        analyticsDayDir: 'desc',
        analyticsPeriod: 'week',
        analyticsCustom: false,
        analyticsFrom: '',
        analyticsTo: '',
        analyticsLoading: false,
        analyticsHelpOpen: false,
        /** График на вкладке Аналитика: обе метрики / только выручка / только заказы */
        analyticsChartMetric: 'both',
        analyticsData: {},
        dashMiniMetric: 'revenue',

        // Живые чаты
        chatList: [],
        chatSearch: '',
        /** G8: фильтр pulse в списке чатов после перехода с дашборда */
        chatPulseFilter: '',
        chatTriageMode: 'active',
        chatPhone: '',
        activeChatPhone: '',
        activeChatTraceId: '',
        chatListLoading: false,
        chatListHasMore: true,
        chatListCursorAt: null,
        chatListCursorId: null,
        /** LRU-кэш сообщений: phone → {messages, hasMore, beforeId, ts}. Макс. 15 чатов. */
        _chatMsgCache: null,
        /** На планшетах/мобилке: выезжающая панель «О клиенте» (на lg — колонка справа) */
        chatMobileInfoOpen: false,
        activeChatState: 'chatting',
        chatMessages: [],
        chatMessagesHasMore: false,
        chatMessagesBeforeId: null,
        chatMessagesLoadingOlder: false,
        operatorInput: '',
        unreadChats: 0,
        /** G5 Live Pulse: bump every 30s on вкладке «Чаты» для пересчёта wait time */
        _chatPulseAt: 0,
        kanbanVisible: { draft: 20, confirmed: 20, sent_to_iiko: 20, in_transit: 20, waiting_pickup: 20, completed: 20 },
        upsellFeedbackLoading: false,
        /** Кабина оператора: сводка по клиенту */
        customerSummaryLoading: false,
        customerSummaryError: null,
        customerSummary: {
            user_exists: false,
            phone: '',
            name: null,
            total_orders: 0,
            revenue_orders: 0,
            total_spent: 0,
            avg_check: 0,
            is_blocked: false,
            ai_paused: false,
            operator_note: '',
            last_escalation: null,
        },
        // p15:context — заказы/брони гостя для правой колонки чата (GET /orders?q, /bookings?q).
        guestContextLoading: false,
        guestContext: {
            activeOrder: null,
            activeBooking: null,
        },
        // p15:delete-modal — кастомное подтверждение удаления заказа.
        orderDeleteModalOpen: false,
        orderDeleteSubmitting: false,
        orderDeleteIds: [],
        orderDeleteRows: [],
        orderDeleteReason: '',
        orderDeleteAck: false,
        orderDeleteDelayReady: false,
        orderDeleteSource: '',
        _orderDeleteDelayTimer: null,
        _p15TourOnResize: null,
        // p15:tour — coach-marks (localStorage per staff email).
        p15TourActive: false,
        p15TourStepIndex: 0,
        p15TourRect: { top: 0, left: 0, width: 0, height: 0 },
        p15TourPopoverStyle: '',
        cannedResponses: [
            { label: 'Адрес', text: 'Подскажите, пожалуйста: забор самовывозом или доставка? Адрес ресторана: [уточните].' },
            { label: 'Задержка', text: 'Приносим извинения — сейчас высокая загрузка. Время ожидания может увеличиться на 15–20 минут. Спасибо за понимание!' },
            { label: 'Меню', text: 'Сейчас пришлю актуальное меню. Позиции из стоп-листа сразу отмечу.' },
            { label: 'Оплата', text: 'Оплата при получении — картой или наличными, как вам удобнее.' },
            { label: 'Менеджер', text: 'Подключаю менеджера — ответит в ближайшие минуты.' },
        ],

        // Меню
        menuCategoryFilter: '',
        menuCategories: [],
        menuSearchQuery: '',
        menuStockFilter: 'all',
        /** Разделы «бар / напитки» для группировки чипов при >20 категорий */
        menuBarCategoryNames: [
            'Чайханчики', 'Напитки', 'Лимонады', 'Кофе', 'Смузи', 'Фреши', 'Добавки', 'Молочные коктейли',
        ],
        menuBulkMode: false,
        menuBulkSelectedIds: [],
        menuBulkSaving: false,
        menuBulkTargetCategory: '',
        menuBulkLongPressTimer: null,
        menuBulkSuppressOpen: false,
        /** Полная очистка меню (POST /api/admin/menu/clear) */
        menuClearLoading: false,
        /** Чипы разделов: на узких экранах по умолчанию скрыты, чтобы не перекрывать карточки */
        menuCategoryChipsOpen: true,
        /** Панель действий/фильтров вкладки «Меню»: на мобилке по умолчанию свёрнута */
        menuToolbarExpanded: false,
        /** Статус интеграций (GET /api/admin/integrations/status). */
        integrationStatus: defaultIntegrationStatus(),
        integrationSyncLoading: false,
        integrationEvents: [],
        /** Онбординг (GET /api/admin/setup-status). */
        setupStatus: { score: 0, steps: [], menu_items: 0, upsell_rules: 0, packaging_rules: 0, knowledge_items: 0 },
        iikoOnboardApiLogin: '',
        iikoOnboardOrgs: [],
        iikoOnboardSelectedOrg: '',
        iikoOnboardTerminal: '',
        iikoOnboardVerifyLoading: false,
        iikoOnboardSetupLoading: false,
        /** iiko Office (SupplyMind inventory) — GET/PATCH /api/admin/organization/iiko-office */
        iikoOfficeConfig: null,
        iikoOfficeDraft: {
            host: '',
            login: '',
            password: '',
            store_id: '',
            department_id: '',
            location_id: '',
        },
        iikoOfficeLoading: false,
        iikoOfficeSaving: false,
        iikoOfficeDirty: false,
        /** Правила допродаж (CRUD). */
        upsellRules: [],
        upsellLoading: false,
        upsellNew: {
            trigger_category: 'напит',
            suggest_category: 'напит',
            min_order_sum: 0,
            max_order_sum: '',
            phrase_template: '',
            sort_order: 0,
        },
        sidebarOpen: false,
        globalSearchOpen: false,
        globalSearchQ: '',
        globalSearchLoading: false,
        globalSearchResults: { orders: [], chats: [], bookings: [] },
        /** Последний успешно запрошенный запрос глобального поиска (избегаем повторного fetch при том же q). */
        globalSearchLastFetchedQ: '',
        /** Счётчик для мемоизации производных списков меню (фильтры не трогают состав items — только ревизию при загрузке/правке). */
        menuViewRevision: 0,
        _menuDerivedSig: null,
        _menuFilteredCache: [],
        _menuDisplayGroupsCache: [],
        _menuKitchenListCache: [],
        _menuBarListCache: [],
        /** Единое отображение статусов заказа / брони */
        statusConfig: {
            draft: { label: 'Черновик', class: 'bg-gray-100 text-gray-600' },
            confirmed: { label: 'Подтверждён', class: 'bg-blue-50 text-blue-600' },
            sending_to_iiko: { label: 'Отправляется на кухню', class: 'bg-amber-50 text-amber-700' },
            sent_to_iiko: { label: 'На кухне', class: 'bg-emerald-50 text-emerald-600' },
            in_transit: { label: 'В пути', class: 'bg-sky-50 text-sky-700' },
            waiting_pickup: { label: 'Ожидает выдачи', class: 'bg-amber-50 text-amber-700' },
            completed: { label: 'Завершён', class: 'bg-emerald-50 text-emerald-600' },
            cancelled: { label: 'Отменён', class: 'bg-red-50 text-red-500' },
            pending: { label: 'Ожидает', class: 'bg-amber-50 text-amber-600' },
        },
        /** Форматирование дат/денег (объект вне реактивного дерева Chart). */
        fmt: adminFormat,
        /** Ошибка загрузки GET /api/admin/menu (401, сеть и т.д.) */
        menuLoadError: '',
        bookingsLoadError: '',
        menuEditOpen: false,
        menuEditSaving: false,
        menuEditForm: {
            id: null,
            name: '',
            category: '',
            description: '',
            tags: '',
            price: 0,
            is_available: true,
            image_url: '',
            portion_kind: 'single',
            serves_min: 1,
            serves_max: 1,
            allergens: '',
            ingredients_summary: '',
            dietary_tags: '',
            upsell_pairs: '',
        },
        /** Порядок разделов как в бумажном меню */
        menuCategoryOrder: [
            'Салаты', 'Выпечка', 'Соусы', 'Блюда на компанию', 'Традиционная кухня', 'Первые блюда',
            'Пицца', 'Паста', 'Горячие блюда', 'Лагман', 'Шашлык', 'Гарнир',
            'Чайханчики', 'Напитки', 'Лимонады', 'Кофе', 'Смузи', 'Фреши', 'Добавки', 'Молочные коктейли',
        ],

        // Тест бота
        testMessages: [],
        testInput: '',
        testLoading: false,

    };
}

/** Геттеры меню/заказов, init, модалки, сделки с заказами */
function adminMixinMenuOrdersUi() {
    return {
        _refreshMenuView() {
            const sig = [
                this.menuViewRevision,
                this.menuCategoryFilter,
                (this.menuSearchQuery || '').trim(),
                this.menuStockFilter,
            ].join('\u0001');
            if (this._menuDerivedSig === sig) return;
            this._menuDerivedSig = sig;

            let list = this.menuItems;
            if (this.menuCategoryFilter) {
                list = list.filter((i) => i.category === this.menuCategoryFilter);
            }
            const q = (this.menuSearchQuery || '').trim().toLowerCase();
            if (q) {
                list = list.filter((i) =>
                    (i.name && i.name.toLowerCase().includes(q)) ||
                    (i.description && String(i.description).toLowerCase().includes(q)) ||
                    (i.category && i.category.toLowerCase().includes(q)),
                );
            }
            if (this.menuStockFilter === 'in') list = list.filter((i) => i.is_available);
            if (this.menuStockFilter === 'out') list = list.filter((i) => !i.is_available);
            this._menuFilteredCache = list;

            const items = list;
            if (this.menuCategoryFilter) {
                this._menuDisplayGroupsCache = [{ title: this.menuCategoryFilter, items }];
            } else {
                const byCat = {};
                for (const i of items) {
                    const c = (i.category && String(i.category).trim()) ? String(i.category).trim() : 'Прочее';
                    if (!byCat[c]) byCat[c] = [];
                    byCat[c].push(i);
                }
                const keysWithItems = Object.keys(byCat).filter((c) => byCat[c].length);
                if (keysWithItems.length === 0) {
                    this._menuDisplayGroupsCache = [];
                } else {
                    const ordered = [];
                    const seen = new Set();
                    for (const c of this.menuCategories) {
                        if (byCat[c]?.length && !seen.has(c)) {
                            ordered.push(c);
                            seen.add(c);
                        }
                    }
                    for (const c of keysWithItems.sort((a, b) => a.localeCompare(b, 'ru'))) {
                        if (!seen.has(c)) {
                            ordered.push(c);
                            seen.add(c);
                        }
                    }
                    this._menuDisplayGroupsCache = ordered.map((c) => ({ title: c, items: byCat[c] }));
                }
            }

            const bar = this.menuBarCategoryNames;
            this._menuKitchenListCache = this.menuCategories.filter((c) => !bar.includes(c));
            this._menuBarListCache = this.menuCategories.filter((c) => bar.includes(c));
        },

        get menuFilteredItems() {
            this._refreshMenuView();
            return this._menuFilteredCache;
        },

        get menuDisplayGroups() {
            this._refreshMenuView();
            return this._menuDisplayGroupsCache;
        },

        get menuStopCount() {
            return this.menuItems.filter((i) => !i.is_available).length;
        },

        /** Пересчёт stopListFilteredItems (явное состояние вместо getter). */
        _recalcStopListFiltered() {
            const q = (this.stopListSearchQuery || '').trim().toLowerCase();
            const items = Array.isArray(this.stopListItems) ? this.stopListItems : [];
            if (!q) {
                this.stopListFilteredItems = items;
                return;
            }
            this.stopListFilteredItems = items.filter((it) =>
                String(it?.name || '').toLowerCase().includes(q) ||
                String(it?.category || '').toLowerCase().includes(q),
            );
        },

        get navSectionsForDisplay() {
            const role = this.effectiveStaffRole();
            const sections = this.navSections || [];
            if (role === 'admin') return [...sections].reverse();
            return sections;
        },

        get kanbanLateStagesCollapsed() {
            if (this.kanbanLateStagesOpen) return false;
            const late = this.kanbanInTransit.length + this.kanbanWaitingPickup.length + this.kanbanCompleted.length;
            const early = this.kanbanDraft.length + this.kanbanConfirmed.length + this.kanbanSent.length;
            return late === 0 && early === 0;
        },

        get menuTopCategories() {
            this._refreshMenuView();
            const counts = new Map();
            for (const item of this.menuItems || []) {
                const cat = String(item?.category || '').trim();
                if (!cat) continue;
                counts.set(cat, (counts.get(cat) || 0) + 1);
            }
            return [...counts.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([name]) => name);
        },

        get chatMessagesDisplay() {
            const src = Array.isArray(this.chatMessages) ? this.chatMessages : [];
            const out = [];
            let run = 0;
            for (let i = 0; i < src.length; i += 1) {
                const msg = src[i];
                if (!this.chatIsSystemMessage(msg)) {
                    run = 0;
                    out.push(msg);
                    continue;
                }
                run += 1;
                const next = src[i + 1];
                if (next && this.chatIsSystemMessage(next)) continue;
                out.push({ ...msg, _collapsed_system: true, _collapsed_count: run });
                run = 0;
            }
            return out;
        },

        get aiCenterShowExtendedTabs() {
            return this.analyticsDensity === 'advanced' || !this.canToggleAnalyticsDensity();
        },

        get kanbanAllEmpty() {
            if (this.ordersLoading) return false;
            return (
                this.kanbanDraft.length
                + this.kanbanConfirmed.length
                + this.kanbanSent.length
                + this.kanbanInTransit.length
                + this.kanbanWaitingPickup.length
                + this.kanbanCompleted.length
            ) === 0;
        },

        inboxClientsPaneLoading() {
            return !!(this.moneyQueueLoading || this.failedTasksLoading);
        },

        inboxClientsInitialLoading() {
            return this.inboxClientsPaneLoading();
        },

        dashboardNowCards() {
            const draftCount = (this.kanbanDraft?.length ?? 0) || Number(this.moneyQueue?.summary?.abandoned_drafts ?? 0);
            return [
                {
                    id: 'inbox',
                    label: 'Требует внимания',
                    count: this.inboxTotalOpen(),
                    tab: 'inbox',
                    hint: 'Открытые задачи',
                },
                {
                    id: 'chats',
                    label: 'Диалоги',
                    count: Number(this.unreadChats || 0),
                    tab: 'chats',
                    hint: 'Непрочитанные',
                },
                {
                    id: 'drafts',
                    label: 'Черновики',
                    count: draftCount,
                    tab: 'orders',
                    hint: 'Заказы в работе',
                },
                {
                    id: 'bookings',
                    label: 'Брони сегодня',
                    count: Number(this.dashStats?.bookings_today ?? this.dashStats?.bookings ?? 0),
                    tab: 'bookings',
                    hint: 'На сегодня',
                },
            ];
        },

        get menuCategoriesKitchenList() {
            this._refreshMenuView();
            return this._menuKitchenListCache;
        },

        get menuCategoriesBarList() {
            this._refreshMenuView();
            return this._menuBarListCache;
        },

        get filteredChatList() {
            const rank = (c) => {
                const p = this.chatPulseStatus(c);
                if (p === 'red') return 0;
                if (p === 'amber') return 1;
                return 2;
            };
            const wait = (c) => Number(this.chatWaitSeconds(c) || 0);
            let sorted = [...this.chatList].sort((a, b) => {
                const dr = rank(a) - rank(b);
                if (dr !== 0) return dr;
                return wait(b) - wait(a);
            });
            const pulseFilter = String(this.chatPulseFilter || '').trim();
            if (pulseFilter === 'red') {
                sorted = sorted.filter((c) => this.chatPulseStatus(c) === 'red');
            } else if (pulseFilter === 'slow') {
                sorted = sorted.filter((c) => {
                    const p = this.chatPulseStatus(c);
                    return p === 'red' || p === 'amber';
                });
            }
            if (!this.chatSearch.trim()) return sorted;
            const q = this.chatSearch.trim().toLowerCase();
            return sorted.filter(c => c.phone.toLowerCase().includes(q));
        },

        _kanbanVisible(list, key) {
            const n = Number(this.kanbanVisible?.[key] || 20);
            return list.slice(0, Math.max(1, n));
        },
        kanbanShowMore(key) {
            if (!this.kanbanVisible) this.kanbanVisible = {};
            this.kanbanVisible[key] = Number(this.kanbanVisible[key] || 20) + 20;
        },
        get kanbanDraft() {
            return this.orders.filter(o => o.status === 'draft');
        },
        get kanbanDraftVisible() {
            return this._kanbanVisible(this.kanbanDraft, 'draft');
        },
        get kanbanConfirmed() {
            return this.orders.filter(o => o.status === 'confirmed');
        },
        get kanbanConfirmedVisible() {
            return this._kanbanVisible(this.kanbanConfirmed, 'confirmed');
        },
        get kanbanSent() {
            return this.orders.filter(o => o.status === 'sent_to_iiko');
        },
        get kanbanSentVisible() {
            return this._kanbanVisible(this.kanbanSent, 'sent_to_iiko');
        },
        get kanbanInTransit() {
            return this.orders.filter(o => o.status === 'in_transit');
        },
        get kanbanInTransitVisible() {
            return this._kanbanVisible(this.kanbanInTransit, 'in_transit');
        },
        get kanbanWaitingPickup() {
            return this.orders.filter(o => o.status === 'waiting_pickup');
        },
        get kanbanWaitingPickupVisible() {
            return this._kanbanVisible(this.kanbanWaitingPickup, 'waiting_pickup');
        },
        get kanbanCompleted() {
            return this.orders.filter(o => o.status === 'completed');
        },
        get kanbanCompletedVisible() {
            return this._kanbanVisible(this.kanbanCompleted, 'completed');
        },

        /** Разбивка по дням на вкладке Аналитика (не трогает порядок точек на графике). */
        _refreshAnalyticsDailySorted() {
            const raw = this.analyticsData.daily || [];
            const sig = [
                this.analyticsDaySort,
                this.analyticsDayDir,
                raw.length,
                this.analyticsDailyDataRev,
            ].join('\u0001');
            if (this._analyticsDailySig === sig) return;
            this._analyticsDailySig = sig;
            const arr = [...raw];
            const col = this.analyticsDaySort;
            const dir = this.analyticsDayDir === 'asc' ? 1 : -1;
            arr.sort((a, b) => {
                if (col === 'date') return dir * a.date.localeCompare(b.date);
                if (col === 'orders') return dir * (a.orders - b.orders);
                return dir * (a.revenue - b.revenue);
            });
            this._analyticsDailySortedCache = arr;
        },
        get sortedAnalyticsDaily() {
            this._refreshAnalyticsDailySorted();
            return this._analyticsDailySortedCache;
        },

        /** Таблица заказов: сортировка по колонкам на клиенте. */
        get ordersTableSorted() {
            const raw = this.orders || [];
            const sig = [
                raw.length,
                this.ordersSort.column,
                this.ordersSort.dir,
                this._ordersLoadSeq,
            ].join('\u0001');
            if (this._ordersSortedSig === sig && Array.isArray(this._ordersSortedCache)) return this._ordersSortedCache;
            this._ordersSortedSig = sig;

            const list = [...raw];
            const col = this.ordersSort.column;
            const dir = this.ordersSort.dir === 'asc' ? 1 : -1;
            const num = (o) => {
                const v = o.items_count;
                if (typeof v === 'number') return v;
                return o.items?.items?.length || 0;
            };
            list.sort((a, b) => {
                let va;
                let vb;
                switch (col) {
                    case 'id':
                        va = a.id;
                        vb = b.id;
                        break;
                    case 'client': {
                        const pa = (a.user_phone || '').toLowerCase();
                        const pb = (b.user_phone || '').toLowerCase();
                        const c = pa.localeCompare(pb, 'ru');
                        if (c !== 0) return dir * c;
                        return dir * String(a.user_name || '').localeCompare(String(b.user_name || ''), 'ru');
                    }
                    case 'items':
                        va = num(a);
                        vb = num(b);
                        break;
                    case 'order_type': {
                        const ta = String(a.order_type || '');
                        const tb = String(b.order_type || '');
                        return dir * ta.localeCompare(tb, 'ru');
                    }
                    case 'total':
                        va = Number(a.total_price);
                        vb = Number(b.total_price);
                        break;
                    case 'status': {
                        const sa = String(a.status || '');
                        const sb = String(b.status || '');
                        return dir * sa.localeCompare(sb, 'ru');
                    }
                    case 'date':
                    default:
                        va = new Date(a.created_at || 0).getTime();
                        vb = new Date(b.created_at || 0).getTime();
                }
                if (va < vb) return -dir;
                if (va > vb) return dir;
                return 0;
            });
            this._ordersSortedCache = list;
            return this._ordersSortedCache;
        },

        async showUiAlert(message, title = 'Внимание') {
            const t = String(title || 'Внимание');
            const m = String(message || '');
            return this.openUiConfirm({
                title: t,
                message: m,
                showCancel: false,
                confirmText: 'Понятно',
            });
        },

        /** Раньше добавляли «Вклад ИИ» динамически — после P1.5.0 пункты фиксированы в navItems. */
        ensureAi2NavItems() {},

        async init() {
            const today = new Date();
            const weekAgo = new Date(today);
            weekAgo.setDate(weekAgo.getDate() - 7);
            this.analyticsTo = today.toISOString().slice(0, 10);
            this.analyticsFrom = weekAgo.toISOString().slice(0, 10);

            try {
                const m = window.matchMedia('(min-width: 768px)').matches;
                this.menuCategoryChipsOpen = false;
                this.menuToolbarExpanded = m;
                // Mobile-first: заказы без горизонтального скролла (канбан — для больших экранов).
                this.ordersView = m ? (this.ordersView || 'kanban') : 'table';
                const savedOpsDensity = localStorage.getItem(ADMIN_OPERATIONS_DENSITY_STORAGE_KEY);
                const savedKanbanDensity = localStorage.getItem('rm_kanban_density_v1');
                const density =
                    savedOpsDensity === 'compact' || savedOpsDensity === 'normal'
                        ? savedOpsDensity
                        : savedKanbanDensity === 'compact' || savedKanbanDensity === 'normal'
                          ? savedKanbanDensity
                          : null;
                if (density) {
                    this.operationsDensity = density;
                    this.kanbanDensity = density;
                }
                const savedAnalyticsDensity = localStorage.getItem(ADMIN_ANALYTICS_DENSITY_STORAGE_KEY);
                if (savedAnalyticsDensity === 'advanced' || savedAnalyticsDensity === 'normal') {
                    this.analyticsDensity = savedAnalyticsDensity;
                    this.dashboardTab = savedAnalyticsDensity === 'advanced' ? 'analytics' : 'overview';
                }
            } catch (_e) {
                this.menuCategoryChipsOpen = true;
                this.menuToolbarExpanded = true;
            }

            await this.checkSession();

            if ('serviceWorker' in navigator) {
                navigator.serviceWorker.register('/static/sw.js').catch(() => {});
            }

            // AudioContext unlock: браузеры блокируют звук до user gesture.
            // Разблокируем по первому клику/тапу, затем можно безопасно играть уведомления.
            try {
                this._audioUnlocked = false;
                window.addEventListener(
                    'pointerdown',
                    () => {
                        this._audioUnlocked = true;
                        try {
                            const ctx = this._audioCtx;
                            if (ctx && ctx.state === 'suspended' && typeof ctx.resume === 'function') {
                                void ctx.resume();
                            }
                        } catch (_e) { /* ignore */ }
                    },
                    { once: true, passive: true },
                );
            } catch (_e) { /* ignore */ }

            // Стоп-лист: держим производный список в явном состоянии (не getter),
            // чтобы Alpine гарантированно перерисовывал сетку при поиске/обновлении данных.
            try {
                this.$watch('stopListSearchQuery', () => this._recalcStopListFiltered());
                this.$watch('stopListItems', () => this._recalcStopListFiltered());
            } catch (_e) {
                // no-op: на случай if Alpine $watch недоступен (не должно быть)
            }

            this._chatPulseAt = Date.now();
            setInterval(() => {
                if (this.currentTab === 'chats') this._chatPulseAt = Date.now();
            }, 30000);

            if (!this._shiftPageHideBound) {
                this._shiftPageHideBound = true;
                window.addEventListener('pagehide', () => {
                    if (this.currentTab === 'shift') this.releaseShiftFocusClaim();
                });
            }
        },

        /** Есть ли активный поиск / раздел / фильтр по наличию (нужна кнопка «Показать всё меню»). */
        menuHasActiveFilters() {
            const q = (this.menuSearchQuery || '').trim();
            return !!(q || (this.menuCategoryFilter || '').trim() || this.menuStockFilter !== 'all');
        },

        /** Сброс фильтров вкладки «Меню» (после «Открыть в меню» со стоп-листа и т.п.). */
        resetMenuFilters() {
            this.menuSearchQuery = '';
            this.menuCategoryFilter = '';
            this.menuStockFilter = 'all';
            this.menuViewRevision += 1;
        },

        /** Переход со стоп-листа: фильтр по позиции; на мобилке чипы разделов свернуты, чтобы было видно карточку. */
        openMenuFromStopItem(item) {
            if (!item) return;
            this.currentTab = 'menu';
            this.menuView = 'catalog';
            this.menuSearchQuery = item.name || '';
            this.menuCategoryFilter = '';
            this.menuStockFilter = 'out';
            try {
                const m = window.matchMedia('(min-width: 768px)').matches;
                this.menuCategoryChipsOpen = m;
                this.menuToolbarExpanded = true;
            } catch (_e) {
                this.menuCategoryChipsOpen = false;
                this.menuToolbarExpanded = true;
            }
            this.loadTabData();
        },

        /**
         * Chart.js: при x-show вкладка была display:none — canvas 0×0. После показа вызываем resize().
         * См. документацию: контейнер должен иметь ненулевой размер, иначе нужен ручной resize().
         */
        _resizeVisibleCharts(tab) {
            try {
                if (tab === 'dashboard' && this.dashboardTab === 'overview' && charts.dashboard) {
                    charts.dashboard.resize();
                    charts.dashboard.update('none');
                }
                if (tab === 'dashboard' && this.dashboardTab === 'analytics' && charts.analytics) {
                    charts.analytics.resize();
                    charts.analytics.update('none');
                }
            } catch (e) {
                adminLogger.warn('Chart resize', e);
            }
        },

        _attachChartLayoutFix(chart, parentEl) {
            if (!chart || !parentEl) return;
            const fix = () => {
                try {
                    chart.resize();
                    chart.update('none');
                } catch (_e) { /* ignore */ }
            };
            fix();
            [40, 120, 400].forEach((ms) => setTimeout(fix, ms));
            if (typeof ResizeObserver !== 'undefined') {
                const ro = new ResizeObserver(() => {
                    const r = parentEl.getBoundingClientRect();
                    if (r.width > 2 && r.height > 2) fix();
                });
                ro.observe(parentEl);
                setTimeout(() => {
                    try { ro.disconnect(); } catch (_e) { /* ignore */ }
                    fix();
                }, 1500);
            }
        },

        /** Универсальный форматтер ошибок FastAPI: принимает detail (строка/массив/объект) или весь response body. */
        formatApiError(detailOrBody, fallback) {
            const raw = detailOrBody != null && typeof detailOrBody === 'object' && 'detail' in detailOrBody
                ? detailOrBody.detail
                : detailOrBody;
            if (raw == null) return fallback || 'Ошибка запроса';
            if (typeof raw === 'string') return raw || fallback || 'Ошибка запроса';
            if (Array.isArray(raw)) {
                const msg = raw.map((e) => (e && typeof e === 'object' && e.msg != null ? String(e.msg) : JSON.stringify(e))).join('; ');
                return msg || fallback || 'Ошибка запроса';
            }
            if (typeof raw === 'object' && typeof raw.message === 'string') return raw.message;
            if (typeof raw === 'object') return JSON.stringify(raw);
            return String(raw) || fallback || 'Ошибка запроса';
        },

        ensureSuperadminAction() {
            if (this.isSuperadmin) return true;
            void this.showUiAlert('Это действие доступно только Super Admin.', 'Недостаточно прав');
            return false;
        },

        /**
         * Модальное подтверждение или ввод текста. Возвращает { ok, value? }.
         * @param {object} opts
         * @param {string} [opts.title]
         * @param {string} [opts.message]
         * @param {boolean} [opts.danger]
         * @param {string} [opts.confirmText]
         * @param {string} [opts.cancelText]
         * @param {boolean} [opts.showCancel]
         * @param {boolean} [opts.showInput]
         * @param {object|null} [opts.input] label, placeholder, value, readonly, required
         */
        openUiConfirm(opts) {
            return new Promise((resolve) => {
                const def = {
                    title: 'Подтверждение',
                    message: '',
                    danger: false,
                    confirmText: 'Подтвердить',
                    cancelText: 'Отмена',
                    showCancel: true,
                    showInput: false,
                    input: null,
                };
                const o = { ...def, ...(opts || {}) };
                const inp = o.input && typeof o.input === 'object' ? o.input : null;
                this._uiConfirmResolve = resolve;
                this.uiConfirmTitle = o.title != null ? String(o.title) : def.title;
                this.uiConfirmMessage = o.message != null ? String(o.message) : '';
                this.uiConfirmDanger = !!o.danger;
                this.uiConfirmConfirmText = o.confirmText != null ? String(o.confirmText) : def.confirmText;
                this.uiConfirmCancelText = o.cancelText != null ? String(o.cancelText) : def.cancelText;
                this.uiConfirmShowCancel = o.showCancel !== false;
                this.uiConfirmShowInput = !!inp || !!o.showInput;
                this.uiConfirmInputReadonly = !!(inp && inp.readonly);
                this.uiConfirmInputLabel = inp && inp.label != null ? String(inp.label) : '';
                this.uiConfirmInputPlaceholder = inp && inp.placeholder != null ? String(inp.placeholder) : '';
                this.uiConfirmInputValue = inp && inp.value != null ? String(inp.value) : '';
                this.uiConfirmInputRequired = !!(inp && inp.required);
                this.uiConfirmInputMultiline = !!(inp && inp.multiline);
                this.uiConfirmInputRows = inp && Number(inp.rows) > 0 ? Number(inp.rows) : 6;
                this.uiConfirmShowFormatJson = !!(inp && inp.multiline);
                this.uiConfirmRequireAck = !!o.requireAck;
                this.uiConfirmAckLabel = o.ackLabel != null ? String(o.ackLabel) : 'Я понимаю, что это действие нельзя отменить';
                this.uiConfirmAckChecked = false;
                this.uiConfirmError = '';
                this.uiConfirmSubmitting = false;
                this.uiConfirmOpen = true;
                queueMicrotask(() => {
                    try {
                        const root = document.querySelector('[data-ui-confirm-panel]');
                        const t = this.uiConfirmInputMultiline
                            ? root?.querySelector('textarea[data-ui-confirm-autofocus]')
                            : root?.querySelector('input[data-ui-confirm-autofocus]');
                        t?.focus?.({ preventScroll: true });
                        if (t && t.select && typeof t.select === 'function' && !this.uiConfirmInputMultiline) {
                            t.select();
                        }
                    } catch (_) { /* ignore */ }
                });
            });
        },
        uiConfirmTryFormatJson() {
            if (!this.uiConfirmShowFormatJson || this.uiConfirmSubmitting) return;
            const raw = String(this.uiConfirmInputValue || '').trim();
            if (!raw) {
                this.uiConfirmError = 'Нет текста для форматирования';
                return;
            }
            try {
                const v = JSON.parse(raw);
                this.uiConfirmInputValue = JSON.stringify(v, null, 2);
                this.uiConfirmError = '';
            } catch {
                this.uiConfirmError = 'Не удалось разобрать JSON. Проверьте кавычки, запятые и скобки.';
            }
        },
        uiConfirmBackdrop() {
            if (!this.uiConfirmOpen || this.uiConfirmSubmitting) return;
            if (this.uiConfirmShowCancel) this.uiConfirmCancel();
            else this.uiConfirmSubmit();
        },
        uiConfirmCancel() {
            if (this.uiConfirmSubmitting) return;
            this.uiConfirmOpen = false;
            const r = this._uiConfirmResolve;
            this._uiConfirmResolve = null;
            if (r) r({ ok: false });
        },
        uiConfirmSubmit() {
            if (this.uiConfirmSubmitting) return;
            if (this.uiConfirmRequireAck && !this.uiConfirmAckChecked) {
                this.uiConfirmError = 'Отметьте согласие, чтобы продолжить';
                return;
            }
            if (this.uiConfirmShowInput && this.uiConfirmInputRequired) {
                const v = (this.uiConfirmInputValue || '').trim();
                if (!v) {
                    this.uiConfirmError = 'Заполните поле';
                    return;
                }
                this.uiConfirmError = '';
            }
            this.uiConfirmOpen = false;
            const r = this._uiConfirmResolve;
            this._uiConfirmResolve = null;
            if (!r) return;
            if (this.uiConfirmShowInput) {
                const raw = this.uiConfirmInputReadonly
                    ? String(this.uiConfirmInputValue || '')
                    : (this.uiConfirmInputMultiline
                        ? String(this.uiConfirmInputValue || '')
                        : (this.uiConfirmInputValue || '').trim());
                r({ ok: true, value: raw });
            } else {
                r({ ok: true });
            }
        },

        /** Стрелка сортировки для заголовка таблицы. */
        sortArrow(activeCol, targetCol, direction) {
            if (activeCol !== targetCol) return '';
            return direction === 'asc' ? '↑' : '↓';
        },

        /** Число календарных дней UTC в интервале аналитики (включительно). */
        analyticsIntervalDayCount() {
            const a = this.analyticsData;
            if (!a?.date_from || !a?.date_to) return 0;
            const t0 = new Date(`${String(a.date_from).slice(0, 10)}T12:00:00Z`).getTime();
            const t1 = new Date(`${String(a.date_to).slice(0, 10)}T12:00:00Z`).getTime();
            if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 < t0) return 0;
            return Math.floor((t1 - t0) / 86400000) + 1;
        },

        _analyticsCalendarDayWord(n) {
            const abs100 = n % 100;
            const rem10 = n % 10;
            if (rem10 === 1 && abs100 !== 11) return 'календарный день';
            if (rem10 >= 2 && rem10 <= 4 && (abs100 < 10 || abs100 >= 20)) return 'календарных дня';
            return 'календарных дней';
        },

        /** Заголовок блока «интервал отчёта» (даты уже через fmt.date). */
        analyticsIntervalTitle() {
            const a = this.analyticsData;
            if (!a?.date_from || !a?.date_to) return '';
            const d0 = this.fmt.date(a.date_from);
            const d1 = this.fmt.date(a.date_to);
            if (a.date_from === a.date_to) return d0;
            return `${d0} — ${d1}`;
        },

        /** Пояснение под заголовком без «период: day» — только суть для оператора. */
        analyticsIntervalSubtitle() {
            const a = this.analyticsData;
            if (!a?.date_from || !a?.date_to) return '';
            const n = this.analyticsIntervalDayCount();
            const word = this._analyticsCalendarDayWord(n);
            return `В отчёт попали ${n} ${word} по данным сервера (UTC). Суммы и график считаются только в этом диапазоне.`;
        },

        ordersIikoErrorCount() {
            return (this.orders || []).filter((o) => o.iiko_last_error).length;
        },

        incidentsTotalOpen() {
            const a = this.attentionSummary;
            if (a && typeof a.total_open === 'number') return Number(a.total_open);
            return Number(this.incidents?.total_open || 0);
        },

        /** Подзаголовок баннера: сначала первая причина из «Интеграции degraded», иначе первая группа. */
        attentionBannerSubtitle() {
            const a = this.attentionSummary;
            const groups = a && Array.isArray(a.groups) ? a.groups : [];
            const integ = groups.find((g) => g && g.id === 'integrations_degraded');
            if (integ && Array.isArray(integ.items) && integ.items.length) {
                const t = integ.items[0].title;
                if (t) return String(t);
            }
            const first = groups[0];
            if (first && Array.isArray(first.items) && first.items.length) {
                const t = first.items[0].title;
                if (t) return String(t);
            }
            return first && first.title ? String(first.title) : '';
        },

        /** Бейдж пункта «Требует внимания»: помощь + деньги на кону + инциденты. */
        inboxTotalOpen() {
            const money = Number(this.moneyQueue?.summary?.total || 0);
            return Number(this.dashStats?.failed_tasks_open || 0) + money + this.incidentsTotalOpen();
        },

        incidentSummaryCount(key) {
            return Number(this.incidents?.summary?.[key] || 0);
        },

        incidentSeverityClass(severity) {
            const s = String(severity || 'info');
            if (s === 'critical') return 'border-red-200 bg-red-50/80 text-red-900';
            if (s === 'warning') return 'border-amber-200 bg-amber-50/80 text-amber-950';
            if (s === 'ok') return 'border-emerald-200 bg-emerald-50/80 text-emerald-900';
            return 'border-slate-200 bg-slate-50 text-slate-700';
        },

        incidentDotClass(severity) {
            const s = String(severity || 'info');
            if (s === 'critical') return 'bg-red-500';
            if (s === 'warning') return 'bg-amber-500';
            if (s === 'ok') return 'bg-emerald-500';
            return 'bg-slate-400';
        },

        incidentSeverityLabel(severity) {
            const s = String(severity || 'info');
            if (s === 'critical') return 'Критично';
            if (s === 'warning') return 'Внимание';
            if (s === 'ok') return 'ОК';
            return 'Инфо';
        },

        incidentMetaText(meta) {
            if (!Array.isArray(meta)) return '';
            return meta
                .filter((m) => m && m.value !== null && m.value !== undefined && String(m.value).trim() !== '')
                .map((m) => `${m.label}: ${m.value}`)
                .join(' · ');
        },

        incidentGroupPreviewItems(group) {
            const items = Array.isArray(group?.items) ? group.items : [];
            return items.slice(0, 4);
        },

        integrationErrorBadge() {
            const s = this.integrationStatus;
            if (!s || typeof s !== 'object') return 0;
            let n = 0;
            if (s.last_menu_sync && s.last_menu_sync.at && !s.last_menu_sync.ok) n += 1;
            if (s.last_stoplist && s.last_stoplist.at && !s.last_stoplist.ok) n += 1;
            return n;
        },

        orderMinutesInStatus(order, col) {
            const iso = col === 'draft'
                ? order.created_at
                : (order.updated_at || order.created_at);
            if (!iso) return 0;
            const t = new Date(iso).getTime();
            if (!Number.isFinite(t)) return 0;
            return Math.max(0, (Date.now() - t) / 60000);
        },

        orderTimerWarn(order, col) {
            return this.orderMinutesInStatus(order, col) > 10;
        },

        orderTimerText(order, col) {
            const m = Math.floor(this.orderMinutesInStatus(order, col));
            const lab = col === 'draft' ? 'черновике' : (col === 'confirmed' ? 'подтверждении' : 'кухне');
            if (m < 1) return `в ${lab}: только что`;
            if (m < 60) return `${m} мин в ${lab}`;
            const h = Math.floor(m / 60);
            return `${h} ч в ${lab}`;
        },

        // p15:failed — счётчик из API list_orders (±1 ч от created_at заказа).
        orderFailedWhatsappCount(order) {
            const n = Number(order?.failed_whatsapp_near_order);
            return Number.isFinite(n) && n > 0 ? n : 0;
        },

        goToGuestChatForOrder(order) {
            const ph = (order?.user_phone || '').trim();
            if (!ph) {
                void this.showUiAlert('У заказа нет телефона гостя.', 'Чат');
                return;
            }
            this.showOrderModal = false;
            this.orderCompositionOpen = false;
            this.navigateToTab('chats');
            setTimeout(() => this.selectChat(ph), 80);
        },

        orderAwaitingOrderPrepay(order) {
            const meta = order?.items?.order_meta;
            return !!(meta && meta.requires_order_prepayment && (order.prepayment_status || '') === 'pending');
        },

        orderPlov1kgHint(order) {
            const fees = order?.items?.fee_lines;
            if (!Array.isArray(fees)) return '';
            const hit = fees.find((f) => f && String(f.kind || '').startsWith('packaging_plov_1kg'));
            if (!hit) return '';
            const n = (hit.name || '').trim();
            return n.length > 24 ? n.slice(0, 22) + '…' : n;
        },

        orderHasUpsell(order) {
            const meta = order?.items?.order_meta;
            if (!meta || typeof meta !== 'object') return false;
            const r = meta.recommendation;
            if (r && typeof r === 'object' && (r.offered || r.reason || r.offered_iiko_id)) return true;
            const tr = meta.recommendation_trace;
            if (!Array.isArray(tr) || !tr.length) return false;
            return tr.some((ev) => ev && typeof ev === 'object' && (ev.offered || ev.offered_iiko_id));
        },

        /** События допродажи для UI (trace + legacy recommendation). */
        orderSalesInsightSteps(order) {
            const meta = order?.items?.order_meta;
            if (!meta || typeof meta !== 'object') return [];
            const tr = meta.recommendation_trace;
            const out = [];
            if (Array.isArray(tr)) {
                for (const ev of tr) {
                    if (!ev || typeof ev !== 'object') continue;
                    if (!ev.offered && !ev.offered_iiko_id) continue;
                    out.push(ev);
                }
            }
            const rec = meta.recommendation;
            if (!out.length && rec && typeof rec === 'object' && (rec.offered || rec.offered_iiko_id)) {
                out.push(rec);
            }
            return out;
        },

        /** Текст аргументации от модели (ИИ), без серверного gastro_hint. */
        salesInsightAiReason(trace) {
            if (!trace || typeof trace !== 'object') return '';
            const reason = String(trace.reason || trace.upsell_reasoning || '').trim();
            if (reason) return reason;
            const src = String(trace.source || '').trim();
            if (src === 'upsell_rule') {
                const rid = trace.rule_id;
                return rid != null && rid !== ''
                    ? `Правило допродаж №${rid}`
                    : 'Правило допродаж из настроек';
            }
            if (src) return src;
            return 'Предложение в диалоге';
        },
        salesInsightGastroHint(trace) {
            if (!trace || typeof trace !== 'object') return '';
            return String(trace.gastro_hint || '').trim();
        },
        salesInsightStrategyLogic(trace) {
            if (!trace || typeof trace !== 'object') return '';
            return String(trace.strategy_logic || '').trim();
        },
        salesInsightShowLogicBlock(trace) {
            return !!(
                (trace && String(trace.gastro_hint || '').trim())
                || (trace && String(trace.strategy_logic || '').trim() === 'Custom AI Choice')
            );
        },
        salesInsightWhy(trace) {
            return this.salesInsightAiReason(trace);
        },

        salesInsightSourceLabel(trace) {
            if (!trace || typeof trace !== 'object') return 'ИИ';
            const src = String(trace.source || '').trim();
            if (src === 'upsell_rule') return 'Правило';
            if (src) return src;
            return 'ИИ';
        },

        /** Сумма принятых допродаж по trace (accepted_revenue_kzt). */
        orderAcceptedUpsellRevenueSum(order) {
            let rev = 0;
            for (const s of this.orderSalesInsightSteps(order)) {
                if (s && s.accepted === true && s.accepted_revenue_kzt != null) {
                    rev += Number(s.accepted_revenue_kzt) || 0;
                }
            }
            return Math.round(rev * 100) / 100;
        },

        /**
         * Доля выручки заказа, пришедшая с принятых допродаж ИИ (по учтённой сумме в trace).
         * null — если нет суммы или итог заказа нулевой.
         */
        orderAiEfficiencyPct(order) {
            const rev = this.orderAcceptedUpsellRevenueSum(order);
            const total = Number(order?.total_price);
            if (!Number.isFinite(total) || total <= 0 || rev <= 0) return null;
            return Math.min(100, Math.round((rev / total) * 1000) / 10);
        },

        orderLowConfidence(order) {
            if (!order) return false;
            if (order.low_confidence === true) return true;
            const c = order.items?.order_meta?.confidence;
            return !!(c && c.low_confidence);
        },

        kanbanOrderSurfaceClass(order, normalClass) {
            if (order?.iiko_last_error) {
                return 'bg-gradient-to-br from-rose-50 via-white to-red-50/40 border-2 border-red-400 border-l-[5px] border-l-red-600 shadow-md ring-2 ring-red-200/90 ring-offset-1 ring-offset-white';
            }
            let c = normalClass;
            if (this.orderLowConfidence(order)) {
                c += ' ds-order-surface--ai-confidence';
            }
            if (this.orderAwaitingOrderPrepay(order)) {
                c += ' ring-2 ring-amber-400 ring-offset-2 ring-offset-white border-amber-400';
            }
            return c;
        },

        setOperationsDensity(mode) {
            const next = mode === 'compact' ? 'compact' : 'normal';
            this.operationsDensity = next;
            this.kanbanDensity = next;
            try {
                localStorage.setItem(ADMIN_OPERATIONS_DENSITY_STORAGE_KEY, next);
                localStorage.setItem('rm_kanban_density_v1', next);
            } catch (_e) {}
        },

        setKanbanDensity(mode) {
            this.setOperationsDensity(mode);
        },

        isOperationsTab() {
            return ADMIN_OPERATIONS_TABS.includes(this.currentTab);
        },

        canToggleOperationsDensity() {
            return this.isOperationsTab();
        },

        operationsCompactEnabled() {
            if (this.operationsDensity !== 'compact' || !this.isOperationsTab()) return false;
            try {
                return window.matchMedia('(min-width: 640px)').matches;
            } catch (_e) {
                return true;
            }
        },

        kanbanCompactEnabled() {
            return this.ordersView === 'kanban' && this.operationsCompactEnabled();
        },

        bookingsSidebarOpen() {
            if ((this.bookings || []).length > 0) return true;
            const day = this.bookingSelectedDate || '';
            if (day && this.bookingCountForDay(day) > 0) return true;
            return false;
        },

        orderPhoneLast4(order) {
            const phone = String(order?.user_phone || '').replace(/\D/g, '');
            return phone ? phone.slice(-4) : '----';
        },

        orderStatusDotClass(order) {
            const status = order?.status || '';
            if (status === 'draft') return 'bg-gray-400';
            if (status === 'confirmed') return 'bg-blue-500';
            if (status === 'sent_to_iiko') return 'bg-emerald-500';
            if (status === 'in_transit') return 'bg-sky-500';
            if (status === 'waiting_pickup') return 'bg-amber-500';
            if (status === 'completed') return 'bg-emerald-700';
            if (status === 'cancelled') return 'bg-red-500';
            return 'bg-gray-300';
        },

        orderTypeCompactLabel(order) {
            const m = { delivery: 'D', pickup: 'P', hall: 'H' };
            return m[order?.order_type] || '-';
        },

        paymentMethodCompactLabel(order) {
            const pd = order?.items?.order_meta?.payment_details;
            if (pd && pd.type === 'mixed') return '+';
            const pm = order?.payment_method || 'cash';
            const m = { cash: '$', card: 'C', remote: 'L' };
            return m[pm] || '?';
        },

        orderCompactTitle(order) {
            const items = order?.items?.items || [];
            const first = items[0]?.name || '';
            if (first && items.length > 1) return `${first} +${items.length - 1}`;
            if (first) return first;
            return `${items.length || 0} позиций`;
        },

        orderTypeBadge(order) {
            if (!order || !order.order_type) return '';
            const m = { delivery: 'ДОСТАВКА', pickup: 'САМОВЫВОЗ', hall: 'В ЗАЛЕ' };
            return m[order.order_type] || String(order.order_type);
        },

        paymentIcon(order) {
            const pd = order?.items?.order_meta?.payment_details;
            if (pd && pd.type === 'mixed') return '🔀';
            if (!order || !order.payment_method) return '💵';
            const m = { cash: '💵', card: '💳', remote: '🔗' };
            return m[order.payment_method] || '💵';
        },

        paymentMethodTitle(order) {
            const pd = order?.items?.order_meta?.payment_details;
            if (pd && pd.type === 'mixed') return 'Смешанная оплата';
            if (!order || !order.payment_method) return 'Наличные';
            const m = {
                cash: 'Наличные',
                card: 'Карта при получении',
                remote: 'Удалённо (Kaspi, Payme, ссылка…)',
            };
            return m[order.payment_method] || order.payment_method;
        },

        paymentMixedSplit(order) {
            const pd = order?.items?.order_meta?.payment_details;
            if (!pd || pd.type !== 'mixed' || !pd.split) return null;
            const s = pd.split;
            const parts = [];
            if (Number(s.cash) > 0) parts.push({ label: 'Наличные', amount: Number(s.cash), icon: '💵' });
            if (Number(s.card) > 0) parts.push({ label: 'Карта', amount: Number(s.card), icon: '💳' });
            if (Number(s.remote) > 0) parts.push({ label: 'Удалённо', amount: Number(s.remote), icon: '🔗' });
            return parts.length ? parts : null;
        },

        paymentMethodShort(order) {
            const pd = order?.items?.order_meta?.payment_details;
            if (pd && pd.type === 'mixed') return 'MIX';
            const pm = order?.payment_method || 'cash';
            const m = { cash: 'НАЛИЧНЫЕ', card: 'КАРТА', remote: 'ОНЛАЙН' };
            return m[pm] || String(pm).toUpperCase();
        },

        paymentKanbanBadgeClass(order) {
            const pd = order?.items?.order_meta?.payment_details;
            const base = 'uppercase';
            if (pd && pd.type === 'mixed') {
                return base + ' bg-gradient-to-r from-fuchsia-100 to-purple-100 text-purple-950 border-purple-400';
            }
            const pm = order?.payment_method || 'cash';
            if (pm === 'card') return base + ' bg-blue-50 text-blue-800 border-blue-200';
            if (pm === 'remote') return base + ' bg-gradient-to-r from-amber-100 to-orange-100 text-amber-950 border-amber-400';
            return base + ' bg-gray-100 text-gray-700 border-gray-300';
        },

        paymentModalPayClass(order) {
            const pd = order?.items?.order_meta?.payment_details;
            if (pd && pd.type === 'mixed') return 'text-purple-800';
            const pm = order?.payment_method || 'cash';
            if (pm === 'card') return 'text-blue-700';
            if (pm === 'remote') return 'text-amber-800';
            return 'text-gray-800';
        },

        prepaymentLabel(st) {
            const m = {
                pending: 'Предоплата: ожидает',
                paid: 'Предоплата: получена',
                waived: 'Предоплата: снята',
                not_required: '',
            };
            return m[st] || (st ? String(st) : '');
        },

        kanbanDragStart(ev, order) {
            try {
                ev.dataTransfer.setData('text/plain', String(order.id));
                ev.dataTransfer.effectAllowed = 'move';
            } catch { /* ignore */ }
        },

        kanbanDragEnd() {
            this._kanbanBlockOpenUntil = Date.now() + 450;
        },

        /**
         * Карточка заказа (канбан, таблица, дашборд): полная модалка с составом и действиями.
         * Раньше метод отсутствовал — клики по канбану ничего не открывали.
         */
        openOrderDetails(order) {
            if (!order || order.id == null) return;
            if (this._kanbanBlockOpenUntil && Date.now() < this._kanbanBlockOpenUntil) return;
            const id = Number(order.id);
            if (!Number.isFinite(id)) return;
            const fresh = (this.orders || []).find((o) => Number(o.id) === id);
            this.selectedOrder = fresh || order;
            this.orderCompositionOpen = false;
            this.orderRebuildError = '';
            this.orderTimelineExpanded = false;
            this.orderTimeline = [];
            this.showOrderModal = true;
            this.$nextTick(() => {
                try {
                    this.initOrderRebuildFromSelected();
                    this.syncOrderPaymentFormFromSelected();
                } catch (_e) { /* ignore */ }
            });
        },

        openManualOrderModal() {
            this.manualOrderError = '';
            this.manualOrderLoading = false;
            this.manualOrderForm = defaultManualOrderForm();
            this.showManualOrderModal = true;
        },

        closeManualOrderModal() {
            this.showManualOrderModal = false;
            this.manualOrderLoading = false;
            this.manualOrderError = '';
        },

        async submitManualOrder() {
            const f = this.manualOrderForm || {};
            const phone = String(f.phone || '').trim();
            if (!phone) {
                this.manualOrderError = 'Укажите телефон гостя.';
                return;
            }
            this.manualOrderLoading = true;
            this.manualOrderError = '';
            try {
                const body = {
                    phone,
                    order_type: String(f.order_type || 'pickup'),
                    payment_mode: String(f.payment_mode || 'single'),
                    payment_method: String(f.payment_method || 'cash'),
                    split_cash: Number(f.split_cash) || 0,
                    split_card: Number(f.split_card) || 0,
                    split_remote: Number(f.split_remote) || 0,
                    delivery_address: String(f.delivery_address || '').trim(),
                    pickup_time_note: String(f.pickup_time_note || '').trim(),
                    food_lines: [],
                };
                const lid = Number(this.selectedLocationId || 0);
                if (lid > 0) body.location_id = lid;
                const { ok, data } = await this.apiJsonResponse('/api/admin/orders/manual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!ok) {
                    this.manualOrderError = this.formatApiError(data.detail || data) || 'Не удалось создать черновик';
                    return;
                }
                this.showManualOrderModal = false;
                await this.loadOrders();
                await this.loadDashStats();
                await this.syncDashboardChartIfVisible();
                if (data && Number.isFinite(Number(data.id))) {
                    const id = Number(data.id);
                    const fresh = (this.orders || []).find((o) => Number(o.id) === id);
                    if (fresh) this.selectedOrder = fresh;
                }
                void this.showUiAlert(`Черновик заказа #${data?.id ?? ''} создан`, 'Готово');
            } catch {
                this.manualOrderError = 'Ошибка сети. Проверьте соединение.';
            } finally {
                this.manualOrderLoading = false;
            }
        },

        canEditOrderComposition(order) {
            if (!order) return false;
            const s = String(order.status || '').toLowerCase();
            return s === 'draft' || s === 'confirmed';
        },

        syncOrderPaymentFormFromSelected() {
            const o = this.selectedOrder;
            this.orderPaymentError = '';
            if (!o?.items?.order_meta) {
                this.orderPayMode = 'single';
                this.orderPayMethod = o?.payment_method || 'cash';
                this.orderPaySplitCash = 0;
                this.orderPaySplitCard = 0;
                this.orderPaySplitRemote = 0;
                return;
            }
            const m = o.items.order_meta;
            const pm = m.payment_mode === 'mixed' ? 'mixed' : 'single';
            this.orderPayMode = pm;
            this.orderPayMethod = m.payment_method || o.payment_method || 'cash';
            const pd = m.payment_details;
            if (pm === 'mixed' && pd && typeof pd === 'object' && pd.type === 'mixed' && pd.split) {
                const sp = pd.split;
                this.orderPaySplitCash = Number(sp.cash) || 0;
                this.orderPaySplitCard = Number(sp.card) || 0;
                this.orderPaySplitRemote = Number(sp.remote) || 0;
            } else {
                this.orderPaySplitCash = 0;
                this.orderPaySplitCard = 0;
                this.orderPaySplitRemote = 0;
            }
        },

        onOrderPayModeChange() {},

        onOrderPayMethodQuickPick() {
            this.orderPayMode = 'single';
        },

        async saveOrderPaymentSplit() {
            if (!this.selectedOrder?.id) return;
            this.orderPaymentSaving = true;
            this.orderPaymentError = '';
            try {
                const body = {
                    payment_mode: this.orderPayMode,
                    payment_method: this.orderPayMethod,
                    split_cash: this.orderPaySplitCash,
                    split_card: this.orderPaySplitCard,
                    split_remote: this.orderPaySplitRemote,
                    expected_version: this.selectedOrder.row_version,
                };
                const { ok, data } = await this.apiJsonResponse(
                    `/api/admin/orders/${this.selectedOrder.id}/payment-split`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    },
                );
                if (!ok) {
                    this.orderPaymentError = typeof data.detail === 'string'
                        ? data.detail
                        : JSON.stringify(data.detail || data);
                    return;
                }
                await this.loadOrders();
                const updated = this.orders.find((o) => o.id === this.selectedOrder.id);
                if (updated) {
                    this.selectedOrder = updated;
                    this.syncOrderPaymentFormFromSelected();
                }
            } catch {
                this.orderPaymentError = 'Ошибка сети';
            } finally {
                this.orderPaymentSaving = false;
            }
        },

        _emptyOrderEditLine() {
            return {
                name: '',
                quantity: 1,
                iiko_item_id: '',
                packaging_plov_1kg: '',
                modifiers_ids: [],
                modifiers_text: '',
                exclude_ingredients: [],
            };
        },

        initOrderCompositionLinesFromSelected() {
            const items = this.selectedOrder?.items?.items;
            if (!Array.isArray(items) || items.length === 0) {
                this.orderEditLines = [this._emptyOrderEditLine()];
                return;
            }
            this.orderEditLines = items.map((it) => ({
                name: it.name || '',
                quantity: Number(it.quantity) || 1,
                iiko_item_id: it.iiko_id || '',
                packaging_plov_1kg: it.packaging_plov_1kg || '',
                modifiers_ids: Array.isArray(it.modifiers_ids) ? [...it.modifiers_ids] : [],
                modifiers_text: Array.isArray(it.modifiers)
                    ? it.modifiers.map((m) => [m.name || '', m.iiko_id || m.id || ''].filter(Boolean).join(':')).filter(Boolean).join(', ')
                    : (Array.isArray(it.modifiers_ids) ? it.modifiers_ids.join(', ') : ''),
                exclude_ingredients: Array.isArray(it.exclude_ingredients) ? [...it.exclude_ingredients] : [],
            }));
        },

        openOrderCompositionEditor() {
            this.orderCompositionOpen = true;
            this.initOrderCompositionLinesFromSelected();
        },

        closeOrderCompositionEditor() {
            this.orderCompositionOpen = false;
        },

        addOrderEditLine() {
            this.orderEditLines.push(this._emptyOrderEditLine());
        },

        removeOrderEditLine(idx) {
            if (this.orderEditLines.length <= 1) return;
            this.orderEditLines.splice(idx, 1);
        },

        parseOrderLineModifiers(line) {
            const raw = String(line?.modifiers_text || '').trim();
            if (!raw) return [];
            return raw.split(',')
                .map((part) => part.trim())
                .filter(Boolean)
                .map((part) => {
                    const pieces = part.split(':').map((x) => x.trim()).filter(Boolean);
                    if (pieces.length >= 2) return { name: pieces[0], iiko_id: pieces.slice(1).join(':') };
                    return { name: pieces[0], iiko_id: pieces[0] };
                });
        },

        async openOrderCompositionJsonModal() {
            this.initOrderRebuildFromSelected();
            const r = await this.openUiConfirm({
                title: 'Редактор состава (JSON)',
                message: 'Массив объектов: name, quantity, iiko_item_id (или iiko_id), packaging_plov_1kg, exclude_ingredients',
                showInput: true,
                showCancel: true,
                confirmText: 'Применить',
                input: {
                    label: 'JSON позиций',
                    value: this.orderRebuildDraftJson || '[]',
                    multiline: true,
                    rows: 14,
                    required: false,
                },
            });
            if (!r.ok) return;
            const raw = String(r.value || '').trim();
            try {
                const parsed = JSON.parse(raw || '[]');
                if (!Array.isArray(parsed)) {
                    void this.showUiAlert('JSON должен быть массивом', 'Ошибка');
                    return;
                }
                const lines = [];
                for (const it of parsed) {
                    if (!it || typeof it !== 'object') continue;
                    lines.push({
                        name: String(it.name || '').trim(),
                        quantity: Math.min(99, Math.max(1, Number(it.quantity) || 1)),
                        iiko_item_id: String(it.iiko_item_id || it.iiko_id || '').trim(),
                        packaging_plov_1kg: String(it.packaging_plov_1kg || '').trim(),
                        modifiers_ids: Array.isArray(it.modifiers_ids) ? it.modifiers_ids : [],
                        modifiers_text: Array.isArray(it.modifiers_ids) ? it.modifiers_ids.join(', ') : '',
                        exclude_ingredients: Array.isArray(it.exclude_ingredients) ? it.exclude_ingredients : [],
                    });
                }
                if (!lines.length) {
                    void this.showUiAlert('Нужна хотя бы одна позиция', 'Ошибка');
                    return;
                }
                this.orderCompositionOpen = true;
                this.orderEditLines = lines;
                this.orderRebuildDraftJson = JSON.stringify(lines, null, 2);
            } catch {
                void this.showUiAlert(
                    'Текст не похож на JSON: проверьте кавычки, запятые и скобки. Кнопка «Форматировать JSON» в модалке поможет выровнять структуру.',
                    'Ошибка',
                );
            }
        },

        async submitOrderCompositionFromLines() {
            if (!this.selectedOrder) return;
            const st = String(this.selectedOrder.status || '').toLowerCase();
            if (st !== 'draft' && st !== 'confirmed') return;
            const food_lines = [];
            for (const line of this.orderEditLines) {
                const name = (line.name || '').trim();
                if (!name) continue;
                food_lines.push({
                    name,
                    quantity: Math.min(99, Math.max(1, Number(line.quantity) || 1)),
                    iiko_item_id: (line.iiko_item_id || '').trim(),
                    packaging_plov_1kg: (line.packaging_plov_1kg || '').trim(),
                    modifiers_ids: this.parseOrderLineModifiers(line).map((m) => m.iiko_id || m.id || m.name).filter(Boolean),
                    modifiers: this.parseOrderLineModifiers(line),
                    exclude_ingredients: Array.isArray(line.exclude_ingredients) ? line.exclude_ingredients : [],
                });
            }
            if (food_lines.length === 0) {
                this.orderRebuildError = 'Добавьте хотя бы одну позицию с названием';
                return;
            }
            await this.submitOrderRebuild({ food_lines, closeComposition: true });
        },

        async kanbanDrop(ev, targetCol) {
            const raw = ev.dataTransfer.getData('text/plain');
            const id = parseInt(raw, 10);
            if (!id) return;
            const order = this.orders.find((o) => o.id === id);
            if (!order) return;
            const cur = (order.status || '').toLowerCase();
            let newStatus = null;
            if (targetCol === 'confirmed' && cur === 'draft') newStatus = 'confirmed';
            else if (targetCol === 'draft' && cur === 'confirmed') newStatus = 'draft';
            else if (targetCol === 'sent_to_iiko' && cur === 'confirmed') newStatus = 'sent_to_iiko';
            else if (targetCol === 'in_transit' && (cur === 'sent_to_iiko' || cur === 'waiting_pickup')) newStatus = 'in_transit';
            else if (targetCol === 'waiting_pickup' && (cur === 'sent_to_iiko' || cur === 'in_transit')) newStatus = 'waiting_pickup';
            else if (targetCol === 'completed' && (cur === 'sent_to_iiko' || cur === 'in_transit' || cur === 'waiting_pickup')) newStatus = 'completed';
            else if (targetCol === 'sent_to_iiko' && (cur === 'in_transit' || cur === 'waiting_pickup')) newStatus = 'sent_to_iiko';
            if (!newStatus) return;
            await this.patchOrderStatus(id, newStatus);
        },

        async patchOrderStatus(orderId, status) {
            try {
                const local = (this.orders || []).find((o) => Number(o.id) === Number(orderId));
                const { ok, data } = await this.apiJsonResponse(`/api/admin/orders/${orderId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        status,
                        expected_version: local?.row_version ?? null,
                    }),
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось обновить статус', 'Ошибка');
                    return false;
                }
                await this.loadOrders();
                await this.loadDashStats();
                await this.syncDashboardChartIfVisible();
                return true;
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
                return false;
            }
        },

        async confirmPrepayment() {
            if (!this.selectedOrder) return;
            const id = this.selectedOrder.id;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/orders/${id}/payment`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prepayment_status: 'paid' }),
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось подтвердить предоплату', 'Ошибка');
                    return;
                }
                this.selectedOrder.prepayment_status = 'paid';
                await this.loadOrders();
            } catch { void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка'); }
        },

        async confirmSendToIiko() {
            if (!this.selectedOrder) return;
            const ok = await this.patchOrderStatus(this.selectedOrder.id, 'sent_to_iiko');
            if (ok) this.showOrderModal = false;
        },

        /** Передача в iiko из канбана/списка без открытия модалки. */
        async createUpsellFeedback(mode = 'forbid') {
            const order = this.selectedOrder;
            if (!order || this.upsellFeedbackLoading) return;
            const trace = this.orderSalesInsightSteps(order)[0] || {};
            const payload = {
                mode,
                item_iiko_id: trace.item_iiko_id || trace.iiko_id || '',
                item_name: trace.item_name || trace.name || '',
                suggest_category: trace.category || trace.suggest_category || '',
                trigger_category: order.items?.items?.[0]?.category || '',
            };
            this.upsellFeedbackLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/orders/${order.id}/feedback/upsell-rule`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data), 'Ошибка');
                    return;
                }
                await this.loadUpsellRules();
                this.flashToast(mode === 'suggest' ? 'Правило допродажи создано' : 'Анти-правило сохранено', 'success', 3500);
            } catch (e) {
                adminLogger.error('createUpsellFeedback', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.upsellFeedbackLoading = false;
            }
        },

        async confirmSendToIikoForOrder(order) {
            if (!order || order.id == null) return;
            if (this.orderAwaitingOrderPrepay(order)) {
                void this.showUiAlert('Сначала подтвердите предоплату по этому заказу.', 'Предоплата');
                return;
            }
            const ok = await this.patchOrderStatus(order.id, 'sent_to_iiko');
            if (ok) {
                this.flashToast(`Заказ #${order.id} передан в iiko`, 'success', 3500);
            }
        },

        async confirmAndDeleteOrdersFromModal() {
            const o = this.selectedOrder;
            if (!o || o.id == null) return;
            await this.confirmAndDeleteOrders([Number(o.id)], 'modal');
        },

        // p15:delete-modal — превью + задержка кнопки вместо общего uiConfirm.
        async confirmAndDeleteOrders(ids, source) {
            const clean = [...new Set((ids || []).map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0))].sort((a, b) => a - b);
            if (!clean.length) return;
            this.openOrderDeleteModal(clean, source || '');
        },

        openOrderDeleteModal(ids, source) {
            const clean = [...new Set((ids || []).map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0))].sort((a, b) => a - b);
            if (!clean.length) return;
            if (this._orderDeleteDelayTimer) {
                clearTimeout(this._orderDeleteDelayTimer);
                this._orderDeleteDelayTimer = null;
            }
            this.orderDeleteModalOpen = true;
            this.orderDeleteSubmitting = false;
            this.orderDeleteIds = clean;
            this.orderDeleteSource = source || '';
            this.orderDeleteReason = '';
            this.orderDeleteAck = false;
            this.orderDeleteDelayReady = false;
            this.orderDeleteRows = clean.map((id) => {
                const o = (this.orders || []).find((x) => Number(x.id) === id)
                    || (this.selectedOrder && Number(this.selectedOrder.id) === id ? this.selectedOrder : null);
                return { id, o };
            });
            this._orderDeleteDelayTimer = setTimeout(() => {
                this._orderDeleteDelayTimer = null;
                this.orderDeleteDelayReady = true;
            }, 1000);
        },

        closeOrderDeleteModal() {
            if (this.orderDeleteSubmitting) return;
            this.orderDeleteModalOpen = false;
            if (this._orderDeleteDelayTimer) {
                clearTimeout(this._orderDeleteDelayTimer);
                this._orderDeleteDelayTimer = null;
            }
            this.orderDeleteDelayReady = false;
        },

        async submitOrderDeleteModal() {
            if (!this.orderDeleteAck || !this.orderDeleteDelayReady || this.orderDeleteSubmitting) return;
            if (!this.orderDeleteIds.length) return;
            this.orderDeleteSubmitting = true;
            try {
                await this._executeOrderDeleteDirect(this.orderDeleteIds, this.orderDeleteSource || '');
            } finally {
                this.orderDeleteSubmitting = false;
                this.closeOrderDeleteModal();
            }
        },

        async _executeOrderDeleteDirect(ids, src) {
            try {
                if (ids.length === 1) {
                    const id = ids[0];
                    const { ok, data } = await this.apiJsonResponse(`/api/admin/orders/${id}/delete`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ confirm: true }),
                    });
                    if (!ok) {
                        await this.openUiConfirm({
                            title: 'Не удалось удалить',
                            message: this.formatApiError(data),
                            showCancel: false,
                            confirmText: 'Понятно',
                        });
                        return;
                    }
                    if (src === 'modal') {
                        this.showOrderModal = false;
                        this.selectedOrder = null;
                    }
                    this.flashToast(`Заказ #${id} удалён`, 'success', 3500);
                } else {
                    const { ok, data } = await this.apiJsonResponse('/api/admin/orders/bulk-delete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ confirm: true, order_ids: ids }),
                    });
                    if (!ok) {
                        await this.openUiConfirm({
                            title: 'Не удалось удалить',
                            message: this.formatApiError(data),
                            showCancel: false,
                            confirmText: 'Понятно',
                        });
                        return;
                    }
                    this.flashToast(`Удалено заказов: ${data.deleted ?? ids.length}`, 'success', 4000);
                }
                this.removeOrderIdsFromLocalState(ids);
                try {
                    await Promise.all([this.loadOrders(), this.loadDashStats(), this.loadSettingsOrders()]);
                    await this.syncDashboardChartIfVisible();
                } catch (refreshErr) {
                    adminLogger.error('[admin] обновление списков после удаления заказа', refreshErr);
                }
            } catch (e) {
                adminLogger.error('[admin] _executeOrderDeleteDirect', e);
                await this.openUiConfirm({
                    title: 'Ошибка',
                    message: 'Ошибка сети. Проверьте соединение.',
                    showCancel: false,
                    confirmText: 'Понятно',
                });
            }
        },

        /**
         * A11y: на канбане стрелки влево/вправо смещают фокус между колонками
         * (колонки с [data-kanban-col] и tabindex="0").
         */
        handleKanbanKeydown(e) {
            if (this.currentTab !== 'orders' || this.ordersView !== 'kanban') return;
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            const root = e.currentTarget;
            if (!root?.querySelectorAll) return;
            const cols = root.querySelectorAll('[data-kanban-col]');
            if (!cols.length) return;
            let idx = -1;
            for (let i = 0; i < cols.length; i++) {
                if (cols[i] === document.activeElement || cols[i].contains(document.activeElement)) {
                    idx = i;
                    break;
                }
            }
            e.preventDefault();
            const delta = e.key === 'ArrowRight' ? 1 : -1;
            if (idx < 0) {
                const t = delta > 0 ? 0 : cols.length - 1;
                cols[t].focus();
                return;
            }
            const next = Math.min(cols.length - 1, Math.max(0, idx + delta));
            cols[next].focus();
        },

    };
}

/** Глобальный поиск, брони, heatmap, вебхук URL */
function adminMixinSearchBookings() {
    return {
        handleGlobalKeydown(e) {
            if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) return;
            if (e.key !== 'Escape') return;
            if (this.commandBarOpen) {
                e.preventDefault();
                this.closeCommandBar();
                return;
            }
            /** Закрытие оверлеев сверху вниз (одна Esc — один слой). */
            if (this.p15TourActive) {
                e.preventDefault();
                this.p15TourSkip();
                return;
            }
            if (this.orderDeleteModalOpen && !this.orderDeleteSubmitting) {
                e.preventDefault();
                this.closeOrderDeleteModal();
                return;
            }
            if (this.uiConfirmOpen) {
                e.preventDefault();
                this.uiConfirmCancel();
                return;
            }
            if (this.settingsPurgeModalOpen && !this.settingsPurgeLoading) {
                e.preventDefault();
                this.settingsPurgeModalOpen = false;
                return;
            }
            if (this.orderCompositionOpen && this.showOrderModal) {
                e.preventDefault();
                this.closeOrderCompositionEditor();
                return;
            }
            if (this.showOrderModal) {
                e.preventDefault();
                this.showOrderModal = false;
                this.orderCompositionOpen = false;
                return;
            }
            if (this.showManualOrderModal) {
                e.preventDefault();
                this.closeManualOrderModal();
                return;
            }
            if (this.showBookingModal) {
                e.preventDefault();
                this.closeBookingModal();
                return;
            }
            if (this.menuEditOpen) {
                e.preventDefault();
                this.closeMenuEdit();
                return;
            }
            if (this.setupChecklistOpen) {
                e.preventDefault();
                this.closeSetupChecklist();
                return;
            }
            if (this.scheduleEditorOpen) {
                e.preventDefault();
                this.closeScheduleEditor();
                return;
            }
            if (this.packagingCreateOpen) {
                e.preventDefault();
                this.closePackagingCreateModal();
                return;
            }
            if (this.showDemoDeleteModal) {
                e.preventDefault();
                this.closeDemoDeleteModal();
                return;
            }
            if (this.analyticsHelpOpen) {
                e.preventDefault();
                this.analyticsHelpOpen = false;
                return;
            }
            if (this.globalSearchOpen) {
                e.preventDefault();
                this.globalSearchOpen = false;
                return;
            }
            if (this.knowledgeEditOpen) {
                e.preventDefault();
                this.closeKnowledgeEdit();
                return;
            }
            if (this.chatMobileInfoOpen) {
                e.preventDefault();
                this.chatMobileInfoOpen = false;
                return;
            }
            if (this.ordersMobileFiltersOpen) {
                e.preventDefault();
                this.ordersMobileFiltersOpen = false;
                return;
            }
            if (this.sidebarOpen) {
                e.preventDefault();
                this.sidebarOpen = false;
                return;
            }
        },

        openGlobalSearch() {
            this.globalSearchOpen = true;
            this.globalSearchQ = '';
            this.globalSearchLastFetchedQ = '';
            this.globalSearchResults = { orders: [], chats: [], bookings: [] };
            this.$nextTick(() => {
                requestAnimationFrame(() => {
                    const el = document.getElementById('global-search-input');
                    if (el) {
                        el.focus();
                        el.select();
                    }
                });
            });
        },

        dismissOrdersViewHint() {
            try {
                localStorage.setItem('rm_orders_view_hint_v1', '1');
            } catch {
                /* ignore */
            }
            this.ordersKanbanHintDismissed = true;
        },

        dismissUiHint(key) {
            const k = String(key || '').trim();
            if (!k) return;
            this.uiHintsDismissed = { ...(this.uiHintsDismissed || {}), [k]: true };
            try {
                localStorage.setItem(
                    ADMIN_UI_HINTS_STORAGE_KEY,
                    JSON.stringify(this.uiHintsDismissed),
                );
            } catch {
                /* ignore */
            }
        },

        uiHintDismissed(key) {
            return !!(this.uiHintsDismissed || {})[String(key || '').trim()];
        },

        shiftStatusHeadline() {
            const state = this.shiftState?.state;
            const human = state ? this.shiftStateLabel(state) : '';
            const op = String(this.orgProfile?.operational_label || '').trim();
            if (human && op) {
                if (state === 'S0' || state === 'S3') {
                    return `Статус: ${human}. ${op}`;
                }
                return `${human}. ${op}`;
            }
            return human || op || 'Смена загружается…';
        },

        activeOrderFiltersCount() {
            let n = 0;
            if ((this.orderFilter || '').trim()) n += 1;
            if ((this.orderSearchQ || '').trim()) n += 1;
            if (this.orderSumMin !== '' && this.orderSumMin != null) n += 1;
            if (this.orderSumMax !== '' && this.orderSumMax != null) n += 1;
            return n;
        },

        resetOrderFilters() {
            this.orderFilter = '';
            this.orderSearchQ = '';
            this.orderSumMin = '';
            this.orderSumMax = '';
            this.ordersPage = 1;
            void this.loadOrders();
        },

        applyMobileOrderFilters() {
            this.ordersPage = 1;
            this.ordersMobileFiltersOpen = false;
            void this.loadOrders();
        },

        async reindexMenuEmbeddings() {
            if (this.menuEmbeddingsReindexLoading) return;
            this.menuEmbeddingsReindexLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/menu/reindex-embeddings', {
                    method: 'POST',
                });
                if (ok && data) {
                    const st = data.embedding_stats || data;
                    const u = st.updated ?? 0;
                    const t = st.total_items ?? '—';
                    this.flashToast(`Индекс меню для ИИ: обновлено ${u} из ${t} позиций`, 'success', 4500);
                } else {
                    this.flashToast((data && data.detail) || 'Не удалось переиндексировать меню', 'error', 5000);
                }
            } catch {
                this.flashToast('Ошибка запроса индекса меню', 'error', 4000);
            } finally {
                this.menuEmbeddingsReindexLoading = false;
            }
        },

        async runGlobalSearch() {
            const q = (this.globalSearchQ || '').trim();
            if (q.length < 3) {
                this.globalSearchLastFetchedQ = '';
                this.globalSearchResults = { orders: [], chats: [], bookings: [] };
                return;
            }
            if (q === this.globalSearchLastFetchedQ) return;
            this.globalSearchLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse(
                    `/api/admin/search?q=${encodeURIComponent(q)}&limit=15`,
                );
                if (ok) {
                    this.globalSearchResults = {
                        orders: data.orders || [],
                        chats: data.chats || [],
                        bookings: data.bookings || [],
                    };
                    this.globalSearchLastFetchedQ = q;
                }
            } catch { /* ignore */ }
            finally {
                this.globalSearchLoading = false;
            }
        },

        globalSearchGoOrders(order) {
            this.globalSearchOpen = false;
            this.currentTab = 'orders';
            this.ordersView = 'table';
            this.orderSearchQ = String(order.user_phone || order.id || '');
            const oid = Number(order?.id);
            this.loadTabData().then(() => {
                if (!Number.isFinite(oid)) return;
                const o = this.orders.find((x) => Number(x.id) === oid);
                if (o) this.openOrderDetails(o);
            });
        },

        globalSearchGoChat(row) {
            this.globalSearchOpen = false;
            this.currentTab = 'chats';
            this.loadTabData().then(() => this.selectChat(row.phone));
        },

        globalSearchGoBooking(b) {
            this.globalSearchOpen = false;
            this.currentTab = 'bookings';
            this.loadTabData().then(() => {
                const found = this.bookings.find((x) => x.id === b.id);
                const row = found || b;
                this.selectedBooking = {
                    ...row,
                    hall: row.hall || 'hall_1',
                    status: row.status ?? 'pending',
                };
                this.showBookingModal = true;
            });
        },

        openBookingModal(b) {
            this.selectedBooking = {
                ...b,
                hall: b.hall || 'hall_1',
                status: b.status ?? 'pending',
            };
            this.showBookingModal = true;
        },

        closeBookingModal() {
            this.showBookingModal = false;
            this.selectedBooking = null;
        },

        /** Склонение «N гостей» для модалки брони. */
        bookingGuestsWord(n) {
            const x = Number(n);
            if (!Number.isFinite(x) || x < 0) return 'гостей';
            const m10 = x % 10;
            const m100 = x % 100;
            if (m100 >= 11 && m100 <= 14) return 'гостей';
            if (m10 === 1) return 'гость';
            if (m10 >= 2 && m10 <= 4) return 'гостя';
            return 'гостей';
        },

        bookingHallLabel(h) {
            const m = { hall_1: 'Зал 1', hall_2: 'Зал 2', vip: 'VIP зал' };
            return m[h] || (h ? String(h) : 'Зал 1');
        },

        _bookingIsoDate(d) {
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, '0');
            const day = String(d.getDate()).padStart(2, '0');
            return `${y}-${m}-${day}`;
        },

        _bookingParseIso(iso) {
            const parts = String(iso || '').slice(0, 10).split('-').map(Number);
            if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return new Date();
            return new Date(parts[0], parts[1] - 1, parts[2]);
        },

        _bookingMondayOf(d) {
            const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
            const wd = x.getDay();
            const diff = wd === 0 ? -6 : 1 - wd;
            x.setDate(x.getDate() + diff);
            return x;
        },

        bookingInitWeekIfNeeded() {
            if (this.bookingWeekAnchor && this.bookingSelectedDate) return;
            const today = new Date();
            this.bookingWeekAnchor = this._bookingIsoDate(this._bookingMondayOf(today));
            this.bookingSelectedDate = this._bookingIsoDate(today);
        },

        bookingWeekDays() {
            const anchor = this._bookingParseIso(this.bookingWeekAnchor);
            const todayIso = this._bookingIsoDate(new Date());
            const days = [];
            for (let i = 0; i < 7; i += 1) {
                const d = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate() + i);
                const iso = this._bookingIsoDate(d);
                days.push({
                    iso,
                    weekday: d.toLocaleDateString('ru-RU', { weekday: 'short' }),
                    dayNum: d.getDate(),
                    isToday: iso === todayIso,
                    isSelected: iso === this.bookingSelectedDate,
                    count: this.bookingCountForDay(iso),
                });
            }
            return days;
        },

        bookingWeekRangeLabel() {
            const days = this.bookingWeekDays();
            if (!days.length) return '';
            const fmt = (iso) => {
                const d = this._bookingParseIso(iso);
                return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
            };
            return `${fmt(days[0].iso)} — ${fmt(days[6].iso)}`;
        },

        bookingCountForDay(iso) {
            const key = String(iso || '').slice(0, 10);
            return this.bookings.filter((b) => String(b.date || '').slice(0, 10) === key).length;
        },

        bookingSelectedDayLabel() {
            const d = this._bookingParseIso(this.bookingSelectedDate);
            return d.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' });
        },

        bookingShiftWeek(delta) {
            const anchor = this._bookingParseIso(this.bookingWeekAnchor);
            anchor.setDate(anchor.getDate() + delta * 7);
            this.bookingWeekAnchor = this._bookingIsoDate(anchor);
            void this.loadBookingsForWeek();
        },

        bookingGoToday() {
            const today = new Date();
            this.bookingWeekAnchor = this._bookingIsoDate(this._bookingMondayOf(today));
            this.bookingSelectedDate = this._bookingIsoDate(today);
            void this.loadBookingsForWeek();
        },

        selectBookingDay(iso) {
            this.bookingSelectedDate = String(iso || '').slice(0, 10);
        },

        openBookingsSettingsSchedule() {
            this.navigateToTab('settings', { settingsTab: 'restaurant' });
        },

        openBookingsBotTest() {
            this.navigateToTab('settings', { settingsTab: 'bot_test' });
        },

        get filteredBookings() {
            return this.bookingsForSelectedDay;
        },

        get bookingsForSelectedDay() {
            const day = String(this.bookingSelectedDate || '').slice(0, 10);
            let list = this.bookings.filter((b) => String(b.date || '').slice(0, 10) === day);
            if (this.bookingStatusFilter !== 'all') {
                list = list.filter((b) => b.status === this.bookingStatusFilter);
            }
            return list;
        },

        bookingStatsFor(status) {
            return this.bookings.filter((b) => b.status === status).length;
        },

        /** Отложенное сохранение зала/статуса брони (debounce — порядок PATCH на сервере стабильнее). */
        scheduleBookingFieldPatch(field) {
            const b = this.selectedBooking;
            if (!b?.id || (field !== 'hall' && field !== 'status')) return;
            if (this._bookingPatchDebounce) clearTimeout(this._bookingPatchDebounce);
            this._bookingPatchDebounce = setTimeout(() => {
                this._bookingPatchDebounce = null;
                this._patchBookingFieldExecute(field);
            }, 400);
        },

        /** Сохранение зала или статуса брони (PATCH с одним полем). */
        async _patchBookingFieldExecute(field) {
            const b = this.selectedBooking;
            if (!b?.id || (field !== 'hall' && field !== 'status')) return;
            const prev = field === 'hall' ? b.hall : b.status;
            const payload = field === 'hall' ? { hall: b.hall } : { status: b.status };
            try {
                const res = await this.apiFetch(`/api/admin/bookings/${b.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    if (field === 'hall') b.hall = prev;
                    else b.status = prev;
                    const ix = this.bookings.findIndex((x) => x.id === b.id);
                    if (ix >= 0) {
                        if (field === 'hall') this.bookings[ix].hall = prev;
                        else this.bookings[ix].status = prev;
                    }
                    await this.openUiConfirm({
                        title: 'Не удалось сохранить',
                        message: this.formatApiError(data.detail) || 'Не удалось сохранить',
                        showCancel: false,
                        confirmText: 'Понятно',
                    });
                    return;
                }
                const ix = this.bookings.findIndex((x) => x.id === b.id);
                if (ix >= 0) {
                    if (field === 'hall') this.bookings[ix].hall = b.hall;
                    else this.bookings[ix].status = b.status;
                }
            } catch (e) {
                if (field === 'hall') b.hall = prev;
                else b.status = prev;
                const ix = this.bookings.findIndex((x) => x.id === b.id);
                if (ix >= 0) {
                    if (field === 'hall') this.bookings[ix].hall = prev;
                    else this.bookings[ix].status = prev;
                }
                adminLogger.error(e);
                await this.openUiConfirm({
                    title: 'Ошибка сети',
                    message: 'Не удалось связаться с сервером. Проверьте соединение.',
                    showCancel: false,
                    confirmText: 'Понятно',
                });
            }
        },

        async copyBookingPhone() {
            const p = this.selectedBooking?.user_phone;
            if (!p) return;
            const toast = () => {
                this.flashToast('Телефон скопирован', 'success', 2500);
            };
            try {
                await navigator.clipboard.writeText(p);
                toast();
            } catch {
                try {
                    const ta = document.createElement('textarea');
                    ta.value = p;
                    ta.setAttribute('readonly', '');
                    ta.style.position = 'fixed';
                    ta.style.left = '-9999px';
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                    toast();
                } catch {
                    void this.showUiAlert('Не удалось скопировать номер', 'Ошибка');
                }
            }
        },

        goToChatFromBooking() {
            const phone = this.selectedBooking?.user_phone;
            if (!phone) return;
            this.closeBookingModal();
            this.currentTab = 'chats';
            this.loadTabData().then(() => this.selectChat(phone));
        },

        async toggleAiPaused() {
            const p = this.activeChatPhone?.trim();
            if (!p || !this.customerSummary.user_exists) return;
            const next = !this.customerSummary.ai_paused;
            try {
                const res = await this.apiFetch(`/api/admin/customers/${encodeURIComponent(p)}/ai-pause`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paused: next }),
                });
                if (res.ok) {
                    this.customerSummary.ai_paused = next;
                    this.activeChatState = next ? 'human_mode' : 'chatting';
                    const ix = this.chatList.findIndex((c) => c.phone === p);
                    if (ix >= 0) this.chatList[ix].state = next ? 'human_mode' : 'chatting';
                } else {
                    const data = await res.json().catch(() => ({}));
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось изменить режим', 'Ошибка');
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            }
        },

        heatmapHours() {
            return [...Array(24).keys()];
        },

        heatmapMax() {
            const m = this.analyticsData?.heatmap?.matrix;
            if (!m || !m.length) return 1;
            let mx = 0;
            for (const row of m) {
                for (const c of row) {
                    if (c > mx) mx = c;
                }
            }
            return mx || 1;
        },

        heatmapCellStyle(val) {
            const mx = this.heatmapMax();
            const v = Number(val) || 0;
            if (v <= 0 || !mx) {
                return 'background:rgba(37, 99, 235, 0.06)';
            }
            // Ненулевая ячейка: минимальная заметность, чтобы один заказ на всю неделю не «пропадал»
            const rel = Math.min(1, v / mx);
            const a = Math.max(0.14, rel);
            const bg = `rgba(37, 99, 235, ${0.08 + a * 0.85})`;
            return `background:${bg}`;
        },

        async copyWebhookUrl() {
            const u = this.integrationStatus?.webhook_url;
            if (!u) {
                void this.showUiAlert('Задайте PUBLIC_BASE_URL в .env — тогда здесь появится полный URL вебхука', 'Подсказка');
                return;
            }
            try {
                await navigator.clipboard.writeText(u);
                this.flashToast('URL скопирован', 'success', 2500);
            } catch {
                await this.openUiConfirm({
                    title: 'Скопируйте URL',
                    message: 'Браузер не дал записать в буфер обмена. Выделите ссылку ниже и скопируйте вручную.',
                    showCancel: false,
                    confirmText: 'Закрыть',
                    showInput: true,
                    input: { label: 'URL', value: u, readonly: true, required: false },
                });
            }
        },
    };
}

/** apiFetch, сессия, интеграции UI, база знаний */
function adminMixinAuthKnowledge() {
    return {
        async apiFetch(url, options = {}) {
            const res = await fetch(url, { credentials: 'include', ...options });
            const u = String(url);
            const isAuthProbe = u.includes('/api/admin/auth/login') || u.includes('/api/admin/auth/me');
            if (res.status === 401 && !isAuthProbe) {
                if (!this.auth401AlertShown) {
                    this.auth401AlertShown = true;
                    this.clearAdminDataAfterAuthLoss();
                    this.authenticated = false;
                    this.wsToken = '';
                    this.wsChannelReady = false;
                    this._clearWsReadyTimer();
                    try {
                        if (this.ws) {
                            this.ws.close();
                            this.ws = null;
                        }
                    } catch { /* ignore */ }
                    try {
                        if (charts.dashboard) {
                            charts.dashboard.destroy();
                            charts.dashboard = null;
                        }
                        if (charts.analytics) {
                            charts.analytics.destroy();
                            charts.analytics = null;
                        }
                    } catch { /* ignore */ }
                    await this.openUiConfirm({
                        title: 'Сессия истекла',
                        message: 'Сессия истекла или доступ запрещён. Войдите снова.',
                        showCancel: false,
                        confirmText: 'Понятно',
                    });
                }
            }
            return res;
        },

        /**
         * Один вызов: apiFetch + парсинг JSON. Модалки не показывает — обрабатывайте ok/status у вызова.
         * Убирает дублирование res.json().catch(() => ({})) по админке.
         */
        async apiJsonResponse(url, fetchOpts = {}) {
            const method = String(fetchOpts.method || 'GET').toUpperCase();
            const headers = { ...(fetchOpts.headers || {}) };
            if (method === 'GET' && this._apiEtagCache) {
                const cachedEtag = this._apiEtagCache[String(url)];
                if (cachedEtag) headers['If-None-Match'] = cachedEtag;
            }
            const res = await this.apiFetch(url, { ...fetchOpts, headers });
            if (res.status === 304) {
                return { ok: true, status: 304, data: null, res, notModified: true };
            }
            const data = await res.json().catch(() => ({}));
            if (method === 'GET' && res.ok) {
                const etag = res.headers.get('ETag');
                if (etag && this._apiEtagCache) this._apiEtagCache[String(url)] = etag;
            }
            // Диагностика "вкладка пустая": если API вернул не-2xx, логируем URL+статус.
            // Уровень warn виден по умолчанию.
            if (!res.ok) {
                try {
                    adminLogger.warn('[api]', String(url), res.status, data);
                } catch (_e) { /* ignore */ }
            }
            return { ok: res.ok, status: res.status, data, res };
        },

        _invalidateApiEtag(url) {
            if (this._apiEtagCache) delete this._apiEtagCache[String(url)];
        },

        async _afterAuthBootstrapLoads() {
            await this.refreshDemoStatus();
            await this.loadTabData();
            if (adminTabNeedsChatList(this.currentTab)) {
                await this.loadChatList();
            }
            this.deferIdleWork(() => {
                void this.loadOrgProfile();
                void this.loadIntegrationStatus();
            }, 900);
        },

        /** Сброс чувствительных данных в памяти при 401 — до любых alert и пока форма входа перекрывает UI. */
        clearAdminDataAfterAuthLoss() {
            this.orders = [];
            this.bookings = [];
            this.menuItems = [];
            this.menuCategories = [];
            this.menuLoadError = '';
            this.chatList = [];
            this.activeChatPhone = '';
            this.chatMobileInfoOpen = false;
            this.chatMessages = [];
            this.analyticsData = {};
            this.intelligenceData = { summary: null, insights: [], snapshot: null };
            this.intelligenceAnswer = '';
            this.intelligenceConversationId = null;
            this.digitalTwin = { snapshot: {} };
            this.digitalTwinSimResult = null;
            this.dashStats = {
                daily_series: [],
                today_revenue: 0,
                today_orders: 0,
                menu_items: 0,
                bookings: 0,
            };
            this.settingsTab = 'restaurant';
            this.dashStatsLoading = false;
            this.dashStatsLoadedOnce = false;
            this.dashRoiSummary = null;
            this.dashRoiLoading = false;
            this.orgProfile = {
                id: null,
                organization_id: null,
                name: '',
                timezone: '',
                currency: '',
                whatsapp_phone_number_id: '',
                telegram_ops_chat_id: '',
                prepayment_legal_text: '',
                review_url_2gis: '',
                review_url_google: '',
            };
            this.orgProfileLoading = false;
            this.integrationStatus = defaultIntegrationStatus();
            this.integrationEvents = [];
            this.incidents = {
                groups: [],
                summary: { critical: 0, warning: 0, info: 0, restricted: 0 },
                total_open: 0,
                severity: 'ok',
                restricted_count: 0,
                generated_at: null,
                is_superadmin: false,
            };
            this.incidentsLoadedOnce = false;
            this.attentionSummary = null;
            this.isSuperadmin = false;
            this.hasDemoData = false;
            this.isDemoSession = false;
            this.menuViewRevision += 1;
            this.brandingDraft = { brand_name: '', brand_color_hex: '#2563eb' };
            this.brandingSaving = false;
            this.brandingLogoPending = null;
            this.brandingLogoPendingLabel = '';
            if (this.brandingPreviewObjectUrl) {
                try { URL.revokeObjectURL(this.brandingPreviewObjectUrl); } catch (_e) { /* ignore */ }
            }
            this.brandingPreviewObjectUrl = '';
            this.brandingApiUnavailable = false;
        },

        /** Текущая роль staff (legacy без staff_id → admin). */
        effectiveStaffRole() {
            return String(this.staffRole || 'admin').trim().toLowerCase();
        },

        canStaffManageSupply() {
            return this.effectiveStaffRole() !== 'operator';
        },

        canStaffAdminOnly() {
            return this.effectiveStaffRole() === 'admin';
        },

        canStaffStartStaffMind() {
            return this.canStaffManageSupply();
        },

        /** Role-first sidebar: operator / manager / admin tab matrix. */
        isTabVisibleForRole(tabId) {
            return adminTabVisibleForRole(tabId, this.effectiveStaffRole());
        },

        isTabPrimaryNavForRole(tabId) {
            return adminTabPrimaryNavForRole(tabId, this.effectiveStaffRole());
        },

        isOperatorSecondaryTab(tabId) {
            return this.effectiveStaffRole() === 'operator' && adminOperatorSecondaryTab(tabId);
        },

        isTabShownInSidebar(item) {
            if (!item) return false;
            if (!this.isTabVisibleForRole(item.id)) return false;
            return this.isTabPrimaryNavForRole(item.id);
        },

        /** Shell v2: operator sees shift as execution kernel; inbox as secondary list. */
        navItemDisplayLabel(item) {
            if (!item) return '';
            if (this.effectiveStaffRole() === 'operator') {
                if (item.id === 'shift') return 'Следующее действие';
                if (item.id === 'inbox') return 'Все риски';
            }
            return item.label || '';
        },

        navItemDisplayDesc(item) {
            if (!item) return '';
            if (this.effectiveStaffRole() === 'operator') {
                if (item.id === 'shift') return 'Один фокус — одно действие по смене';
                if (item.id === 'inbox') return 'Полный список рисков и сигналов';
            }
            return item.desc || '';
        },

        isNavExecutionPrimary(tabId) {
            return this.effectiveStaffRole() === 'operator' && tabId === 'shift';
        },

        resolveOperatorLandingTab() {
            return adminResolveOperatorLandingTab(this.shiftState);
        },

        resolveDefaultTabForRole() {
            const role = this.effectiveStaffRole();
            if (role === 'operator') return this.resolveOperatorLandingTab();
            return adminDefaultTabForRole(role);
        },

        canToggleAnalyticsDensity() {
            return this.canStaffManageSupply();
        },

        setAnalyticsDensity(next) {
            const d = next === 'advanced' ? 'advanced' : 'normal';
            this.analyticsDensity = d;
            this.dashboardTab = d === 'advanced' ? 'analytics' : 'overview';
            this._persistAnalyticsDensity();
            if (this.currentTab === 'dashboard') void this.loadTabData();
        },

        _persistAnalyticsDensity() {
            try {
                window.localStorage?.setItem(ADMIN_ANALYTICS_DENSITY_STORAGE_KEY, this.analyticsDensity);
            } catch (_e) { /* ignore */ }
        },

        /** Мобильный tab-bar «Ещё»: подсветка вторичных вкладок по роли. */
        bottomNavMoreTabActive() {
            const tabs = ['bookings'];
            if (this.effectiveStaffRole() === 'operator') {
                tabs.push('orders', 'chats', 'bookings');
            } else {
                tabs.push('inbox');
            }
            if (this.isTabVisibleForRole('ai_center')) tabs.push('ai_center');
            if (this.isTabVisibleForRole('settings')) tabs.push('settings');
            if (this.isTabVisibleForRole('marketing')) tabs.push('marketing');
            return tabs.includes(this.currentTab);
        },

        bottomNavShowsPrimaryTab(tabId) {
            if (!this.isTabVisibleForRole(tabId)) return false;
            if (this.effectiveStaffRole() === 'operator' && adminOperatorSecondaryTab(tabId)) return false;
            return true;
        },

        /** Фоновый poll shift/state для badge риска вне вкладки «Смена». */
        shouldPollShiftStateBadge() {
            if (!this.isTabVisibleForRole('shift')) return false;
            if (this.currentTab === 'shift') return true;
            const m = this.shiftState?.metrics;
            const risk = Number(m?.risk_kzt ?? 0);
            if (risk > 0) return true;
            if (this.shiftState?.focus?.id) return true;
            if (Number(m?.at_risk_count ?? 0) > 0) return true;
            return false;
        },

        _syncShiftStatePolling() {
            if (this.currentTab === 'shift') {
                this._startShiftStateAutoRefresh();
                return;
            }
            this._stopShiftHeartbeat(true);
            if (this._shiftStateRefreshTimer) {
                clearInterval(this._shiftStateRefreshTimer);
                this._shiftStateRefreshTimer = null;
            }
            if (!this.shouldPollShiftStateBadge()) return;
            this._shiftStateRefreshTimer = setInterval(() => {
                if (document.hidden) return;
                void this.loadShiftState(false);
            }, 45000);
        },

        shiftIsCalmEmpty() {
            const ss = this.shiftState;
            if (!ss || ss.focus?.id) return false;
            const risk = Number(ss.metrics?.risk_kzt ?? 0);
            if (risk > 0) return false;
            const st = String(ss.state || '');
            return st === 'S0' || st === 'S3';
        },

        async _afterAuthTabBootstrap() {
            if (this.isTabVisibleForRole('shift') && this.effectiveStaffRole() !== 'operator') {
                await this.loadShiftState(false);
            }
            this._syncShiftStatePolling();
        },

        /** После auth: умный landing оператора (shift при риске/focus, иначе inbox). */
        async applyRoleDefaultLanding(fromHashTab) {
            if (fromHashTab) return;
            if (this.effectiveStaffRole() === 'operator') {
                await this.loadShiftState(true);
                this.currentTab = this.resolveOperatorLandingTab();
            } else {
                this.currentTab = adminDefaultTabForRole(this.effectiveStaffRole());
            }
            this._bootstrapAdminMode({ tabFromHash: null });
            this._pushAdminHash();
        },

        /** Подсказка RBAC для UI: '' если доступ есть, иначе текст для operator/manager. */
        staffRbacHint(level) {
            const role = this.effectiveStaffRole();
            if (level === 'admin') {
                return role === 'admin' ? '' : 'Только для admin';
            }
            if (level === 'manager') {
                return role === 'operator' ? 'Только для admin/manager' : '';
            }
            return '';
        },

        normalizeMePayload(data) {
            const root = data && typeof data === 'object' ? data : {};
            return {
                id: root.id ?? null,
                email: root.email || '',
                role: String(root.role || 'operator').toLowerCase(),
                is_superadmin: !!root.is_superadmin,
                tenant_owner_id: root.tenant_owner_id ?? null,
                active_organization_id: root.active_organization_id ?? null,
                available_organizations: Array.isArray(root.available_organizations) ? root.available_organizations : [],
                available_locations: Array.isArray(root.available_locations) ? root.available_locations : [],
                tenant: root.tenant || null,
                branding: root.branding || null,
                ws_token: root.ws_token || '',
                is_network: !!root.is_network,
                network_orgs: Array.isArray(root.network_orgs) ? root.network_orgs : [],
            };
        },

        ensureSelectedLocationAllowed() {
            const locations = Array.isArray(this.userData?.available_locations) ? this.userData.available_locations : [];
            if (!locations.length) {
                this.selectedLocationId = '';
                return;
            }
            const selected = Number(this.selectedLocationId || 0);
            if (selected && locations.some((l) => Number(l.id) === selected)) return;
            this.selectedLocationId = locations.length === 1 ? String(locations[0].id) : '';
        },

        get activeLocationId() {
            return this.selectedLocationId;
        },

        set activeLocationId(value) {
            this.selectedLocationId = value == null ? '' : String(value);
        },

        locationQueryParams() {
            const p = new URLSearchParams();
            const lid = Number(this.selectedLocationId || 0);
            if (lid > 0) p.set('location_id', String(lid));
            return p;
        },

        locationQueryString(prefix = '?') {
            const qs = this.locationQueryParams().toString();
            return qs ? `${prefix}${qs}` : '';
        },

        async onLocationFilterChanged() {
            this.revenueLeak = adminDefaultRevenueLeak();
            this.dashStatsLoadedOnce = false;
            this.dashActivity = [];
            this.attentionSummary = null;
            this.attentionSummaryFetchedAt = 0;
            this.moneyQueue = adminDefaultMoneyQueue();
            this.voiceCallLogs = [];
            this.voiceCallLogsTotal = 0;
            this.voiceCallLogsOffset = 0;
            this.voiceCallLogsHasMore = false;
            this.shiftState = adminDefaultShiftState();
            this.shiftStateFetchedAt = 0;
            this.shiftStateDegraded = false;
            this.shiftStateLoadError = '';
            this.osDashboardData = null;
            this.intelligenceData = { summary: null, insights: [], snapshot: null };
            this.digitalTwin = { snapshot: null };
            this.chatList = [];
            this.activeChatPhone = '';
            this.chatMessages = [];
            this.chatListHasMore = true;
            this.chatListCursorAt = null;
            this.chatListCursorId = null;
            this.ordersPage = 1;
            this.orders = [];
            this.selectedOrder = null;
            this.showOrderModal = false;
            await Promise.all([
                this.loadRevenueLeak(),
                this.currentTab === 'dashboard' ? this.loadTabData() : Promise.resolve(),
                this.currentTab === 'ai_center' ? this.loadTabData() : Promise.resolve(),
                this.currentTab === 'orders' ? this.loadOrders() : Promise.resolve(),
                this.currentTab === 'chats' ? this.loadChatList(true) : Promise.resolve(),
                this.currentTab === 'inbox' ? this.loadTabData() : Promise.resolve(),
                this.currentTab === 'shift' ? this.loadShiftState(true) : Promise.resolve(),
            ]);
        },

        async selectOrganization(orgId) {
            if (!orgId || (this.orgProfile && orgId === this.orgProfile.id)) return;
            this.orgSwitchChromeDimmed = true;
            this.aiValueLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/auth/select-org', {
                    method: 'POST',
                    body: JSON.stringify({ organization_id: orgId }),
                });
                if (!ok) {
                    void this.showUiAlert(`Не удалось переключить филиал: ${data?.detail || status}`, 'Ошибка');
                    return;
                }
                const me = this.normalizeMePayload(data);
                this.userData = me;
                this.selectedLocationId = '';
                this.ensureSelectedLocationAllowed();
                this.syncBrandingDraftFromUser();
                this.wsToken = me.ws_token;
                this.staffRole = me.role;
                this.isSuperadmin = me.is_superadmin;

                this.connectWebSocket();
                await Promise.all([
                    this.loadOrgProfile(),
                    this.loadTabData(),
                    this.loadIntegrationStatus(),
                    adminTabNeedsChatList(this.currentTab) ? this.loadChatList() : Promise.resolve(),
                ]);
                this.setToast('Филиал переключен');
            } catch (e) {
                adminLogger.error('[admin] selectOrganization', e);
            } finally {
                this.aiValueLoading = false;
                this.orgSwitchChromeDimmed = false;
            }
        },

        /** Phase 1 OS: переключиться в контекст конкретного филиала сети.
         *
         * 1. Проверяет принадлежность филиала к сети (network/switch — guard).
         * 2. Явно закрывает WebSocket ДО переключения — иначе оператор может
         *    получить real-time события чужого заведения в окне между переключением
         *    сессии и переподключением WS.
         * 3. selectOrganization получает новый ws_token и пересоздаёт WS.
         */
        async switchNetworkOrg(orgId) {
            if (!orgId || !this.userData?.is_network) return;
            if (this.orgProfile && orgId === this.orgProfile.id) return;
            this.orgSwitchChromeDimmed = true;

            // Явно разрываем WS до переключения — защита от cross-org событий
            try {
                if (this.ws) {
                    this.ws.onopen = null;
                    this.ws.onclose = null;
                    this.ws.onerror = null;
                    this.ws.onmessage = null;
                    this.ws.close();
                }
                this._wsTokenInUse = null; // сбрасываем guard — force reconnect
            } catch (_e) { /* noop */ }

            try {
                // Guard: проверяем принадлежность org к сети tenant-а
                const { ok, status, data } = await this.apiJsonResponse(`/api/admin/network/switch/${orgId}`, {
                    method: 'POST',
                });
                if (!ok) {
                    void this.showUiAlert(`Не удалось переключить филиал: ${data?.detail || status}`, 'Ошибка');
                    return;
                }
                // selectOrganization: получает новый ws_token → connectWebSocket()
                await this.selectOrganization(orgId);
            } catch (e) {
                adminLogger.error('[admin] switchNetworkOrg', e);
            } finally {
                this.orgSwitchChromeDimmed = false;
            }
        },

        async checkSession() {
            try {
                const res = await this.apiFetch('/api/admin/auth/me');
                if (res.ok) {
                    const data = await res.json();
                    if (!data?.authenticated) {
                        this.authenticated = false;
                        this.wsToken = '';
                        return;
                    }
                    const me = this.normalizeMePayload(data);
                    this.userData = me;
                    this.ensureSelectedLocationAllowed();
                    this.syncBrandingDraftFromUser();
                    this.authenticated = true;
                    this.auth401AlertShown = false;
                    this.wsToken = me.ws_token;
                    this.isDemoSession = !!data.is_demo;
                    this.staffRole = me.role;
                    this.isSuperadmin = me.is_superadmin;
                    this._ensureAdminHashListener();
                    const parsed = adminParseLocationHash();
                    if (!parsed.tab) {
                        await this.applyRoleDefaultLanding(null);
                    } else {
                        this._applyParsedHash(parsed);
                        this._bootstrapAdminMode({ tabFromHash: this.currentTab });
                    }
                    this._installAdminHashWatch();
                    this.connectWebSocket();
                    await this._afterAuthBootstrapLoads();
                    await this._consumePendingHashChatPhone();
                    await this._afterAuthTabBootstrap();
                    this.$nextTick(() => this.maybeStartP15CoachTour());
                } else {
                    this.authenticated = false;
                    this.wsToken = '';
                }
            } catch {
                this.authenticated = false;
                this.wsToken = '';
            }
        },

        async submitLogin() {
            this.loginError = '';
            this.auth401AlertShown = false;
            this.loginLoading = true;
            try {
                const u = this.loginUsername.trim();
                const res = await this.apiFetch('/api/admin/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: u,
                        email: u.includes('@') ? u : '',
                        password: this.loginPassword,
                    }),
                });
                let data = {};
                try { data = await res.json(); } catch { /* empty */ }
                if (!res.ok) {
                    this.loginError = typeof data.detail === 'string' ? data.detail : 'Неверный логин или пароль';
                    return;
                }
                const me = this.normalizeMePayload(data);
                this.userData = me;
                this.ensureSelectedLocationAllowed();
                this.syncBrandingDraftFromUser();
                this.auth401AlertShown = false;
                this.authenticated = true;
                this.wsToken = me.ws_token;
                this.loginPassword = '';
                this.isDemoSession = false;
                this.staffRole = me.role;
                this.isSuperadmin = me.is_superadmin;
                this._ensureAdminHashListener();
                const parsedLogin = adminParseLocationHash();
                if (!parsedLogin.tab) {
                    await this.applyRoleDefaultLanding(null);
                } else {
                    this._applyParsedHash(parsedLogin);
                    this._bootstrapAdminMode({ tabFromHash: this.currentTab });
                }
                this._installAdminHashWatch();
                this.connectWebSocket();
                await this._afterAuthBootstrapLoads();
                await this._consumePendingHashChatPhone();
                await this._afterAuthTabBootstrap();
                this.$nextTick(() => this.maybeStartP15CoachTour());
            } catch {
                this.loginError = 'Не удалось связаться с сервером';
            } finally {
                this.loginLoading = false;
            }
        },

        async submitDemoLogin() {
            this.loginError = '';
            this.auth401AlertShown = false;
            this.loginLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/auth/demo-login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                });
                let data = {};
                try { data = await res.json(); } catch { /* empty */ }
                if (!res.ok) {
                    this.loginError = typeof data.detail === 'string' ? data.detail : 'Не удалось открыть демо';
                    return;
                }
                this.authenticated = true;
                this.wsToken = data.ws_token || '';
                this.isDemoSession = true;
                this.staffRole = String(data.staff_role || 'operator').toLowerCase();
                this.isSuperadmin = !!data.is_superadmin;
                this._ensureAdminHashListener();
                const parsedLogin = adminParseLocationHash();
                if (!parsedLogin.tab) {
                    await this.applyRoleDefaultLanding(null);
                } else {
                    this._applyParsedHash(parsedLogin);
                    this._bootstrapAdminMode({ tabFromHash: this.currentTab });
                }
                this._installAdminHashWatch();
                this.connectWebSocket();
                await this._afterAuthBootstrapLoads();
                await this._consumePendingHashChatPhone();
                await this._afterAuthTabBootstrap();
                this.$nextTick(() => this.maybeStartP15CoachTour());
            } catch {
                this.loginError = 'Не удалось связаться с сервером';
            } finally {
                this.loginLoading = false;
            }
        },

        async loadOrgProfile() {
            this.orgProfileLoading = true;
            try {
                const { ok, status, data, notModified } = await this.apiJsonResponse('/api/admin/organization/profile');
                if (notModified) return;
                if (!ok) {
                    adminLogger.warn('GET /api/admin/organization/profile', status, data);
                    return;
                }
                const scheduleObj = (data?.schedule_json && typeof data.schedule_json === 'object') ? data.schedule_json : {};
                this.orgProfile = {
                    id: data?.id ?? null,
                    organization_id: data?.organization_id ?? null,
                    name: (data?.name || '').trim(),
                    timezone: (data?.timezone || '').trim(),
                    currency: (data?.currency || '').trim(),
                    whatsapp_phone_number_id: (data?.whatsapp_phone_number_id || '').trim(),
                    telegram_ops_chat_id: (data?.telegram_ops_chat_id || '').trim(),
                    prepayment_legal_text: String(data?.prepayment_legal_text ?? '').trim(),
                    review_url_2gis: String(data?.review_url_2gis ?? '').trim(),
                    review_url_google: String(data?.review_url_google ?? '').trim(),
                    schedule_json: scheduleObj,
                    schedule_json_text: JSON.stringify(scheduleObj, null, 2),
                    operational_label: String(data?.operational_label || '').trim(),
                    is_business_open: !!data?.is_business_open,
                    is_kitchen_open: !!data?.is_kitchen_open,
                    force_closed: !!data?.force_closed,
                    force_closed_until: data?.force_closed_until || null,
                    force_closed_reason: String(data?.force_closed_reason || '').trim(),
                };
                this.orgProfileDirty = false;
            } finally {
                this.orgProfileLoading = false;
            }
        },

        async saveOrgProfile() {
            if (this.orgProfileSaving) return;
            const nm = String(this.orgProfile?.name || '').trim();
            if (nm.length < 2) {
                void this.showUiAlert('Название ресторана должно быть не короче 2 символов.', 'Подсказка');
                return;
            }
            this.orgProfileSaving = true;
            try {
                const scheduleJson = (this.orgProfile?.schedule_json && typeof this.orgProfile.schedule_json === 'object')
                    ? this.orgProfile.schedule_json
                    : {};
                const body = {
                    name: nm,
                    timezone: String(this.orgProfile?.timezone || '').trim(),
                    currency: String(this.orgProfile?.currency || '').trim(),
                    whatsapp_phone_number_id: String(this.orgProfile?.whatsapp_phone_number_id || '').trim(),
                    telegram_ops_chat_id: String(this.orgProfile?.telegram_ops_chat_id || '').trim(),
                    prepayment_legal_text: String(this.orgProfile?.prepayment_legal_text ?? '').trim(),
                    review_url_2gis: String(this.orgProfile?.review_url_2gis ?? '').trim(),
                    review_url_google: String(this.orgProfile?.review_url_google ?? '').trim(),
                    schedule_json: scheduleJson,
                };
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/organization/profile', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!ok) {
                    const msg = this.formatApiError(data?.detail) || `Не удалось сохранить (${status})`;
                    void this.showUiAlert(msg, 'Ошибка');
                    return;
                }
                this._invalidateApiEtag('/api/admin/organization/profile');
                this.orgProfile = {
                    id: data?.id ?? this.orgProfile?.id ?? null,
                    organization_id: data?.organization_id ?? this.orgProfile?.organization_id ?? null,
                    name: (data?.name || nm).trim(),
                    timezone: (data?.timezone || '').trim(),
                    currency: (data?.currency || '').trim(),
                    whatsapp_phone_number_id: (data?.whatsapp_phone_number_id || '').trim(),
                    telegram_ops_chat_id: (data?.telegram_ops_chat_id || '').trim(),
                    prepayment_legal_text: String(data?.prepayment_legal_text ?? '').trim(),
                    review_url_2gis: String(data?.review_url_2gis ?? '').trim(),
                    review_url_google: String(data?.review_url_google ?? '').trim(),
                    schedule_json: (data?.schedule_json && typeof data.schedule_json === 'object') ? data.schedule_json : scheduleJson,
                    schedule_json_text: JSON.stringify((data?.schedule_json && typeof data.schedule_json === 'object') ? data.schedule_json : scheduleJson, null, 2),
                    operational_label: String(data?.operational_label || '').trim(),
                    is_business_open: !!data?.is_business_open,
                    is_kitchen_open: !!data?.is_kitchen_open,
                };
                this.orgProfileDirty = false;
            } finally {
                this.orgProfileSaving = false;
            }
        },

        openForceCloseModal() {
            this.forceCloseMinutes = 60;
            this.forceCloseReason = '';
            this.forceCloseOpen = true;
        },

        async submitForceClose() {
            if (this.forceCloseSaving || !this.forceCloseMinutes) return;
            this.forceCloseSaving = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/force-close', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ minutes: this.forceCloseMinutes, reason: this.forceCloseReason }),
                });
                if (!ok) { void this.showUiAlert(this.formatApiError(data) || 'Ошибка', 'Ошибка'); return; }
                this.orgProfile = { ...this.orgProfile,
                    force_closed: !!data?.force_closed,
                    force_closed_until: data?.force_closed_until || null,
                    force_closed_reason: data?.force_closed_reason || '',
                    operational_label: data?.operational_label || '',
                    is_business_open: !!data?.is_business_open,
                    is_kitchen_open: !!data?.is_kitchen_open,
                };
                this.forceCloseOpen = false;
                this.flashToast('Заведение временно закрыто', 'warning', 4000);
            } finally {
                this.forceCloseSaving = false;
            }
        },

        async liftForceClose() {
            if (this.forceCloseSaving) return;
            this.forceCloseSaving = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/force-close', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ minutes: 0 }),
                });
                if (!ok) { void this.showUiAlert(this.formatApiError(data) || 'Ошибка', 'Ошибка'); return; }
                this.orgProfile = { ...this.orgProfile,
                    force_closed: false,
                    force_closed_until: null,
                    force_closed_reason: '',
                    operational_label: data?.operational_label || '',
                    is_business_open: !!data?.is_business_open,
                    is_kitchen_open: !!data?.is_kitchen_open,
                };
                this.flashToast('Заведение снова открыто', 'success', 3000);
            } finally {
                this.forceCloseSaving = false;
            }
        },


        normalizeBrandingColorHex(raw) {
            let s = String(raw ?? '').trim();
            if (!s) return '#2563eb';
            if (!s.startsWith('#')) s = `#${s}`;
            if (/^#[0-9A-Fa-f]{6}$/.test(s)) return s.toLowerCase();
            if (/^#[0-9A-Fa-f]{3}$/.test(s)) {
                const r = s[1];
                const g = s[2];
                const b = s[3];
                return `#${r}${r}${g}${g}${b}${b}`.toLowerCase();
            }
            return '#2563eb';
        },

        syncBrandingDraftFromUser() {
            const b = this.userData?.branding;
            this.brandingDraft = {
                brand_name: b?.brand_name != null ? String(b.brand_name) : '',
                brand_color_hex: this.normalizeBrandingColorHex(b?.brand_color_hex),
            };
            if (this.brandingPreviewObjectUrl) {
                try { URL.revokeObjectURL(this.brandingPreviewObjectUrl); } catch (_e) { /* ignore */ }
            }
            this.brandingPreviewObjectUrl = '';
            this.brandingLogoPending = null;
            this.brandingLogoPendingLabel = '';
            this.brandingDirty = false;
            try {
                const el = this.$refs?.brandingLogoInput;
                if (el) el.value = '';
            } catch (_e) { /* ignore */ }
            this.applyTenantBrandingDocument();
        },

        /** Синхронизирует `--tenant-accent` с `userData.branding` (после /auth/me и сохранения бренда). */
        applyTenantBrandingDocument() {
            try {
                const raw = this.userData?.branding?.brand_color_hex;
                const hx = this.normalizeBrandingColorHex(raw);
                if (typeof window.restoMindApplyTenantAccent === 'function') {
                    window.restoMindApplyTenantAccent(hx);
                } else {
                    document.documentElement.style.setProperty('--tenant-accent', hx);
                }
            } catch (_e) { /* ignore */ }
        },

        headerBrandAvatarStyle() {
            const raw = this.userData?.branding?.brand_color_hex;
            const bg = raw ? this.normalizeBrandingColorHex(raw) : '#2563eb';
            return { backgroundColor: bg };
        },

        headerBrandInitialLetter() {
            const bn = String(this.userData?.branding?.brand_name || '').trim();
            const on = String(this.orgProfile?.name || 'R').trim();
            const s = bn || on;
            const ch = s.slice(0, 1).toUpperCase();
            return ch || 'R';
        },

        headerOperationalEmoji() {
            const p = this.orgProfile || {};
            if (p.force_closed) return '⛔';
            if (p.is_kitchen_open) return '🟢';
            if (p.is_business_open) return '🟡';
            return '⚫️';
        },

        headerOperationalBadgeClass() {
            const p = this.orgProfile || {};
            if (p.force_closed) return 'bg-red-50 border-red-200 text-red-800';
            if (p.is_kitchen_open) return 'bg-emerald-50 border-emerald-200 text-emerald-800';
            if (p.is_business_open) return 'bg-amber-50 border-amber-200 text-amber-800';
            return 'bg-slate-50 border-slate-200 text-slate-700';
        },

        headerOperationalText() {
            const p = this.orgProfile || {};
            if (p.force_closed) return 'Временно закрыто';
            if (p.is_kitchen_open) return 'Открыто';
            if (p.is_business_open) return 'Принимаем';
            return 'Закрыто';
        },

        headerOperationalTitle() {
            const p = this.orgProfile || {};
            if (p.force_closed) {
                const reason = String(p.force_closed_reason || '').trim();
                return reason ? `Временно закрыто: ${reason}` : 'Временно закрыто';
            }
            return String(p.operational_label || '').trim() || this.headerOperationalText();
        },

        brandingPreviewTitle() {
            const b = String(this.brandingDraft?.brand_name || '').trim();
            return b || String(this.orgProfile?.name || 'Ресторан').trim();
        },

        brandingPreviewInitials() {
            const t = this.brandingPreviewTitle();
            const parts = t.split(/\s+/).filter(Boolean);
            if (parts.length >= 2) return `${parts[0][0] || ''}${parts[1][0] || ''}`.toUpperCase().slice(0, 2);
            return t.slice(0, 2).toUpperCase();
        },

        brandingPreviewHeaderStyle() {
            const hx = this.normalizeBrandingColorHex(this.brandingDraft?.brand_color_hex);
            return { boxShadow: `inset 0 0 0 1px ${hx}40` };
        },

        brandingPreviewLogoSrc() {
            if (this.brandingPreviewObjectUrl) return this.brandingPreviewObjectUrl;
            const u = this.userData?.branding?.brand_logo_url;
            return u ? String(u) : '';
        },

        onBrandingLogoSelected(ev) {
            const input = ev?.target;
            const f = input?.files?.[0];
            if (this.brandingPreviewObjectUrl) {
                try { URL.revokeObjectURL(this.brandingPreviewObjectUrl); } catch (_e) { /* ignore */ }
            }
            this.brandingPreviewObjectUrl = '';
            if (!f) {
                this.brandingLogoPending = null;
                this.brandingLogoPendingLabel = '';
                return;
            }
            if (f.size > 1024 * 1024) {
                void this.showUiAlert('Файл больше 1 МБ — выберите PNG или JPG меньшего размера.', 'Ошибка');
                input.value = '';
                return;
            }
            const okMime = f.type === 'image/png' || f.type === 'image/jpeg';
            if (!okMime) {
                void this.showUiAlert('Нужен PNG или JPG.', 'Ошибка');
                input.value = '';
                return;
            }
            this.brandingLogoPending = f;
            this.brandingLogoPendingLabel = `Выбран файл: ${f.name}`;
            this.brandingPreviewObjectUrl = URL.createObjectURL(f);
            this.brandingDirty = true;
        },

        async refreshAuthMeBranding() {
            const res = await this.apiFetch('/api/admin/auth/me');
            if (!res.ok) return;
            const data = await res.json();
            if (!data?.authenticated) return;
            const me = this.normalizeMePayload(data);
            this.userData = me;
            this.ensureSelectedLocationAllowed();
            this.wsToken = me.ws_token;
            this.syncBrandingDraftFromUser();
        },

        async saveBranding() {
            if (this.brandingSaving || this.isDemoSession) return;
            const hex = this.normalizeBrandingColorHex(this.brandingDraft?.brand_color_hex);
            this.brandingDraft.brand_color_hex = hex;
            this.brandingSaving = true;
            try {
                const body = {
                    brand_name: String(this.brandingDraft?.brand_name || '').trim() || null,
                    brand_color_hex: hex,
                };
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/branding', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (status === 404 || status === 405) {
                    this.brandingApiUnavailable = true;
                    void this.showUiAlert(
                        'Серверное API брендинга ещё не развёрнуто (этап E2.2.B). Предпросмотр обновляется локально; после обновления платформы сохранение заработает.',
                        'Подсказка',
                    );
                    return;
                }
                if (!ok) {
                    const msg = this.formatApiError(data?.detail) || `Не удалось сохранить (${status})`;
                    void this.showUiAlert(msg, 'Ошибка');
                    return;
                }
                this.brandingApiUnavailable = false;
                let logoWarn = '';
                if (this.brandingLogoPending) {
                    const fd = new FormData();
                    fd.append('file', this.brandingLogoPending);
                    const res = await this.apiFetch('/api/admin/branding/logo', { method: 'POST', body: fd });
                    if (!res.ok) {
                        if (res.status === 404 || res.status === 405) this.brandingApiUnavailable = true;
                        logoWarn = res.status === 404 || res.status === 405
                            ? 'Текст сохранён; загрузка лого будет доступна после E2.2.B.'
                            : 'Текст сохранён; файл лого не принят сервером.';
                    }
                }
                await this.refreshAuthMeBranding();
                this.brandingDirty = false;
                if (logoWarn) void this.showUiAlert(logoWarn, 'Внимание');
                else this.setToast('Брендинг сохранён');
            } finally {
                this.brandingSaving = false;
            }
        },

        tzBadgeLabel(tz) {
            const zone = String(tz || '').trim() || 'Etc/GMT-5';
            try {
                const fmt = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'shortOffset' });
                const parts = fmt.formatToParts(new Date());
                const off = parts.find((p) => p.type === 'timeZoneName')?.value || '';
                const m = off.match(/GMT([+-]\d{1,2})(?::(\d{2}))?/i);
                if (m) {
                    const hh = Number(m[1]);
                    const mm = Number(m[2] || '00');
                    const sign = hh >= 0 ? '+' : '-';
                    const absH = Math.abs(hh);
                    const label = `UTC${sign}${absH}${mm ? `:${String(mm).padStart(2,'0')}` : ''}`;
                    const place = zone === 'Etc/GMT-5' ? '' : '';
                    return `${label}${place}`;
                }
            } catch { /* ignore */ }
            const place = zone === 'Etc/GMT-5' ? 'UTC+5' : `UTC (${zone})`;
            return place;
        },

        _defaultDay() {
            return { is_closed: false, open: '11:00', kitchen_close: '22:30', business_close: '23:00' };
        },
        normalizeSchedule(raw) {
            let fallbackUsed = false;
            const out = {};
            const input = (raw && typeof raw === 'object') ? raw : {};
            for (const d of this.scheduleDayRows) {
                const src = (input[d.key] && typeof input[d.key] === 'object') ? input[d.key] : null;
                if (!src) fallbackUsed = true;
                const def = this._defaultDay();
                out[d.key] = {
                    is_closed: !!(src && typeof src.is_closed === 'boolean' ? src.is_closed : def.is_closed),
                    open: String(src && src.open ? src.open : def.open),
                    kitchen_close: String(src && src.kitchen_close ? src.kitchen_close : def.kitchen_close),
                    business_close: String(src && src.business_close ? src.business_close : def.business_close),
                };
                if (!out[d.key].open || !out[d.key].kitchen_close || !out[d.key].business_close) {
                    fallbackUsed = true;
                    out[d.key].open = out[d.key].open || def.open;
                    out[d.key].kitchen_close = out[d.key].kitchen_close || def.kitchen_close;
                    out[d.key].business_close = out[d.key].business_close || def.business_close;
                }
            }
            return { schedule: out, fallbackUsed };
        },
        _todayScheduleKey() {
            const map = { Mon: 'mon', Tue: 'tue', Wed: 'wed', Thu: 'thu', Fri: 'fri', Sat: 'sat', Sun: 'sun' };
            try {
                const zone = String(this.orgProfile?.timezone || '').trim();
                const weekday = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: zone || undefined }).format(new Date());
                return map[weekday] || this.scheduleDayRows[new Date().getDay() === 0 ? 6 : new Date().getDay() - 1]?.key || 'mon';
            } catch {
                const jsDay = new Date().getDay();
                return this.scheduleDayRows[jsDay === 0 ? 6 : jsDay - 1]?.key || 'mon';
            }
        },
        scheduleTodayLabel(raw) {
            const normalized = this.normalizeSchedule(raw).schedule;
            const d = normalized[this._todayScheduleKey()] || this._defaultDay();
            if (d.is_closed) return 'Сегодня выходной';
            return `Сегодня ${d.open || '11:00'}–${d.business_close || '23:00'}, кухня до ${d.kitchen_close || '22:30'}`;
        },
        scheduleWeekCompact(raw) {
            const normalized = this.normalizeSchedule(raw).schedule;
            const openDays = [];
            for (const d of this.scheduleDayRows) {
                const day = normalized[d.key] || this._defaultDay();
                if (!day.is_closed) openDays.push(`${d.label}: ${day.open}-${day.business_close}`);
            }
            if (!openDays.length) return 'Вся неделя отмечена как выходная.';
            return openDays.slice(0, 3).join(' · ') + (openDays.length > 3 ? ` · ещё ${openDays.length - 3}` : '');
        },
        openScheduleEditor() {
            const normalized = this.normalizeSchedule(this.orgProfile?.schedule_json);
            this.scheduleEditorOpen = true;
            this.scheduleEditorFallbackUsed = normalized.fallbackUsed;
            this.scheduleEditor = JSON.parse(JSON.stringify(normalized.schedule));
        },
        closeScheduleEditor() {
            this.scheduleEditorOpen = false;
            this.scheduleEditorFallbackUsed = false;
            this.scheduleEditor = {};
        },
        applyMondayToWeek() {
            const src = this.scheduleEditor?.mon || this._defaultDay();
            for (const d of this.scheduleDayRows) {
                if (d.key === 'mon') continue;
                const day = this.scheduleEditor[d.key] || this._defaultDay();
                if (!day.is_closed) {
                    day.open = String(src.open || '11:00');
                    day.kitchen_close = String(src.kitchen_close || '22:30');
                    day.business_close = String(src.business_close || '23:00');
                }
                this.scheduleEditor[d.key] = day;
            }
        },
        _hmToMin(hm) {
            const s = String(hm || '').trim();
            const parts = s.split(':');
            if (parts.length !== 2) return null;
            const hh = Number(parts[0]);
            const mm = Number(parts[1]);
            if (!Number.isFinite(hh) || !Number.isFinite(mm)) return null;
            if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return null;
            return hh * 60 + mm;
        },
        _relCloseFromOpen(openMin, closeMin) {
            if (openMin == null || closeMin == null) return null;
            return closeMin >= openMin ? closeMin : (closeMin + 1440);
        },
        isDayInvalid(dayKey) {
            const d = (this.scheduleEditor && this.scheduleEditor[dayKey]) ? this.scheduleEditor[dayKey] : null;
            if (!d || d.is_closed) return false;
            const o = this._hmToMin(d.open);
            const k = this._hmToMin(d.kitchen_close);
            const b = this._hmToMin(d.business_close);
            if (o == null || k == null || b == null) return true;
            const kRel = this._relCloseFromOpen(o, k);
            const bRel = this._relCloseFromOpen(o, b);
            if (kRel == null || bRel == null) return true;
            return kRel > bRel;
        },
        hasScheduleValidationErrors() {
            for (const d of this.scheduleDayRows) {
                if (this.isDayInvalid(d.key)) return true;
            }
            return false;
        },
        applyScheduleFromEditor() {
            if (this.hasScheduleValidationErrors()) {
                void this.showUiAlert('Исправьте ошибки в графике: прием заказов до не может быть позже закрытия.', 'Ошибка');
                return;
            }
            const payload = {};
            for (const d of this.scheduleDayRows) {
                const src = this.scheduleEditor[d.key] || this._defaultDay();
                payload[d.key] = {
                    is_closed: !!src.is_closed,
                    open: String(src.open || '11:00'),
                    kitchen_close: String(src.kitchen_close || '22:30'),
                    business_close: String(src.business_close || '23:00'),
                };
            }
            this.orgProfile.schedule_json = payload;
            this.orgProfileDirty = true;
            this.closeScheduleEditor();
        },

        async logoutAdmin() {
            try {
                // Сбрасываем флаг до закрытия сокета, чтобы onclose не планировал реконнект.
                this.authenticated = false;
                if (this.ws) {
                    this.ws.close();
                    this.ws = null;
                }
                await this.apiFetch('/api/admin/auth/logout', { method: 'POST' });
            } catch { /* ignore */ }
            this.wsToken = '';
            this._adminHashWatchInstalled = false;
            this.wsChannelReady = false;
            this._clearWsReadyTimer();
            this.staffRole = '';
            this.isSuperadmin = false;
            this.incidentsLoadedOnce = false;
            this.incidents = {
                groups: [],
                summary: { critical: 0, warning: 0, info: 0, restricted: 0 },
                total_open: 0,
                severity: 'ok',
                restricted_count: 0,
                generated_at: null,
                is_superadmin: false,
            };
            this.hasDemoData = false;
            this.auth401AlertShown = false;
            this.orgProfile = {
                id: null,
                organization_id: null,
                name: '',
                timezone: '',
                currency: '',
                whatsapp_phone_number_id: '',
                telegram_ops_chat_id: '',
                prepayment_legal_text: '',
                review_url_2gis: '',
                review_url_google: '',
                schedule_json: null,
                schedule_json_text: '',
            };
        },

        async refreshDemoStatus() {
            try {
                const res = await this.apiFetch('/api/admin/demo/status');
                if (res.ok) {
                    const d = await res.json();
                    this.hasDemoData = !!d.has_demo;
                }
            } catch { /* ignore */ }
        },

        integrationStoplistDotClass() {
            const s = this.integrationStatus;
            if (!s?.iiko_configured) return 'bg-gray-300';
            const ls = s.last_stoplist;
            if (!ls || !ls.at) return 'bg-amber-400';
            return ls.ok
                ? 'bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.25)]'
                : 'bg-red-500 shadow-[0_0_0_3px_rgba(239,68,68,0.25)]';
        },

        integrationStoplistCaption() {
            const s = this.integrationStatus;
            if (!s?.iiko_configured) {
                return 'Настройте iiko (филиал или .env), чтобы видеть статус стоп-листа.';
            }
            const ls = s.last_stoplist;
            if (!ls || !ls.at) {
                return 'Ожидание первой синхронизации стоп-листа…';
            }
            const ok = ls.ok ? 'успешно' : 'ошибка';
            return `Последний запрос стоп-листа: ${ok} · ${this.fmt.date(ls.at)}, ${this.fmt.time(ls.at)}`;
        },

        /** Безопасное слияние ответа API: spread `d` не должен затирать last_* значением undefined/null. */
        mergeIntegrationStatus(d) {
            if (!d || typeof d !== 'object') return;
            const base = defaultIntegrationStatus();
            const {
                last_stoplist: rawStop,
                last_menu_sync: rawMenu,
                payment_providers: rawPP,
                ...rest
            } = d;
            const last_stoplist =
                rawStop != null && typeof rawStop === 'object'
                    ? { ...base.last_stoplist, ...rawStop }
                    : { ...base.last_stoplist };
            const last_menu_sync =
                rawMenu != null && typeof rawMenu === 'object'
                    ? { ...base.last_menu_sync, ...rawMenu }
                    : { ...base.last_menu_sync };
            const payment_providers =
                rawPP != null && typeof rawPP === 'object'
                    ? {
                        freedom_pay: { ...base.payment_providers.freedom_pay, ...(rawPP.freedom_pay || {}) },
                        kaspi: { ...base.payment_providers.kaspi, ...(rawPP.kaspi || {}) },
                        cloudpayments: { ...base.payment_providers.cloudpayments, ...(rawPP.cloudpayments || {}) },
                    }
                    : { ...base.payment_providers };
            this.integrationStatus = {
                ...base,
                ...rest,
                last_stoplist,
                last_menu_sync,
                payment_providers,
            };
        },

        async onPrepaymentEnforcedToggle(ev) {
            const el = ev && ev.target;
            if (!el) return;
            const nextVal = !!el.checked;
            this.orgPrepaymentEnforcedSaving = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/prefs', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prepayment_enforced: nextVal }),
                });
                if (!ok) {
                    el.checked = !nextVal;
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось сохранить', 'Ошибка');
                    return;
                }
                this.mergeIntegrationStatus({
                    ...this.integrationStatus,
                    prepayment_enforced: data.prepayment_enforced !== false,
                });
            } catch (e) {
                el.checked = !nextVal;
                adminLogger.error('[admin] onPrepaymentEnforcedToggle', e);
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            } finally {
                this.orgPrepaymentEnforcedSaving = false;
            }
        },

        async onAutoSendIikoAfterPaymentToggle(ev) {
            const el = ev && ev.target;
            if (!el) return;
            const nextVal = !!el.checked;
            this.orgAutoIikoSaving = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/prefs', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ auto_send_to_iiko_after_payment: nextVal }),
                });
                if (!ok) {
                    el.checked = !nextVal;
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось сохранить', 'Ошибка');
                    return;
                }
                this.mergeIntegrationStatus({
                    ...this.integrationStatus,
                    auto_send_to_iiko_after_payment: !!data.auto_send_to_iiko_after_payment,
                });
            } catch (e) {
                el.checked = !nextVal;
                adminLogger.error('[admin] onAutoSendIikoAfterPaymentToggle', e);
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            } finally {
                this.orgAutoIikoSaving = false;
            }
        },

        async togglePaymentProvider(slug, newEnabled) {
            this.paymentProviderSaving = slug;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/payment-providers', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: slug, enabled: newEnabled }),
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось сохранить', 'Ошибка');
                    return;
                }
                const pp = { ...this.integrationStatus.payment_providers };
                pp[slug] = { ...pp[slug], enabled: !!newEnabled };
                this.mergeIntegrationStatus({ ...this.integrationStatus, payment_providers: pp });
            } catch (e) {
                adminLogger.error('[admin] togglePaymentProvider', e);
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            } finally {
                this.paymentProviderSaving = null;
            }
        },

        paymentProviderWebhookUrl(slug) {
            const base = (this.integrationStatus.webhook_url || '').replace('/api/whatsapp/webhook', '');
            return base ? `${base}/api/webhooks/payment/providers/${slug}` : `/api/webhooks/payment/providers/${slug}`;
        },

        async copyPaymentWebhookUrl(slug) {
            const url = this.paymentProviderWebhookUrl(slug);
            try {
                await navigator.clipboard.writeText(url);
                void this.showUiAlert('URL скопирован', '');
            } catch {
                void this.showUiAlert(url, 'Ссылка для уведомлений об оплате');
            }
        },

        async loadKnowledgeBase() {
            this.knowledgeLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/knowledge');
                const data = await res.json().catch(() => ({}));
                if (res.ok) {
                    this.knowledgeItems = Array.isArray(data.items) ? data.items : [];
                } else {
                    this.knowledgeItems = [];
                }
            } catch (e) {
                adminLogger.error('[admin] loadKnowledgeBase', e);
                this.knowledgeItems = [];
            } finally {
                this.knowledgeLoading = false;
            }
        },

        openKnowledgeCreate() {
            this.knowledgeEditError = '';
            this.knowledgeEditForm = {
                id: null,
                category: '',
                knowledge_kind: 'facility',
                question: '',
                answer: '',
                is_active: true,
                sort_order: 0,
            };
            this.knowledgeEditOpen = true;
        },

        openKnowledgeEdit(k) {
            if (!k) return;
            this.knowledgeEditError = '';
            const kk = (k.knowledge_kind || 'facility').toLowerCase() === 'persona' ? 'persona' : 'facility';
            this.knowledgeEditForm = {
                id: k.id,
                category: k.category || '',
                knowledge_kind: kk,
                question: k.question || '',
                answer: k.answer || '',
                is_active: !!k.is_active,
                sort_order: Number(k.sort_order) || 0,
            };
            this.knowledgeEditOpen = true;
        },

        closeKnowledgeEdit() {
            if (this.knowledgeSaveLoading) return;
            this.knowledgeEditOpen = false;
            this.knowledgeEditError = '';
        },

        async saveKnowledgeItem() {
            const f = this.knowledgeEditForm;
            if (!((f.question || '').trim()) || !((f.answer || '').trim())) {
                this.knowledgeEditError = 'Заполните вопрос и ответ';
                return;
            }
            this.knowledgeSaveLoading = true;
            this.knowledgeEditError = '';
            try {
                const kk = (f.knowledge_kind || 'facility').toLowerCase() === 'persona' ? 'persona' : 'facility';
                const payload = {
                    category: (f.category || '').trim(),
                    knowledge_kind: kk,
                    question: f.question.trim(),
                    answer: f.answer.trim(),
                    is_active: !!f.is_active,
                    sort_order: Number(f.sort_order) || 0,
                };
                let res;
                if (f.id) {
                    res = await this.apiFetch(`/api/admin/knowledge/${f.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                } else {
                    res = await this.apiFetch('/api/admin/knowledge', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                }
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    this.knowledgeEditError = typeof data.detail === 'string' ? data.detail : 'Ошибка сохранения';
                    return;
                }
                this.knowledgeEditOpen = false;
                await this.loadKnowledgeBase();
                await this.loadSetupStatus();
            } catch (e) {
                adminLogger.error('[admin] saveKnowledgeItem', e);
                this.knowledgeEditError = 'Ошибка сети';
            } finally {
                this.knowledgeSaveLoading = false;
            }
        },

        async deleteKnowledgeItem(id) {
            if (id == null) return;
            const { ok } = await this.openUiConfirm({
                title: 'База знаний',
                message: 'Удалить эту запись из базы знаний?',
                danger: true,
            });
            if (!ok) return;
            try {
                // POST — часть прокси режет HTTP DELETE; эндпоинт тот же по смыслу, что DELETE /knowledge/{id}
                const res = await this.apiFetch(`/api/admin/knowledge/${id}/delete`, { method: 'POST' });
                if (res.ok) {
                    await this.loadKnowledgeBase();
                    await this.loadSetupStatus();
                }
                else {
                    const data = await res.json().catch(() => ({}));
                    void this.showUiAlert(this.formatApiError(data), 'Ошибка');
                }
            } catch (e) {
                adminLogger.error('[admin] deleteKnowledgeItem', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            }
        },
    };
}

/** Упаковка, синхронизации, демо, индикатор WS */
function adminMixinPackagingIntegrationsDemoWsUi() {
    return {
        // ─── Packaging Rules ────────────────────────
        packagingPreviewLoading: false,
        packagingPreviewMenuItemId: null,
        packagingPreviewQty: 1,
        packagingPreviewOrderType: 'delivery', // delivery | pickup | hall
        packagingPreviewPlovChoice: '',
        packagingPreviewResult: null,

        initPackagingPreviewDefaults() {
            if (!Array.isArray(this.menuItems) || !this.menuItems.length) return;
            const stillValid = this.packagingPreviewMenuItemId != null
                && this.menuItems.some((x) => x && x.id === this.packagingPreviewMenuItemId);
            if (stillValid) return;
            const first = this.menuItems.find((x) => x && x.id != null);
            if (first) this.packagingPreviewMenuItemId = first.id;
        },

        /**
         * Эвристика: выбранное блюдо — «плов 1кг» → имеет смысл показывать выбор
         * контейнера (tabak / foil_kazan). Смотрим по name/category.
         * Логика совпадает с серверной `classify_packaging_kind` для ветки plov_1kg,
         * чтобы UI не предлагал пользователю переключатель, который всё равно
         * не повлияет на расчёт.
         */
        get packagingPreviewIsPlov1Kg() {
            const id = this.packagingPreviewMenuItemId;
            if (id == null || !Array.isArray(this.menuItems)) return false;
            const item = this.menuItems.find((x) => x && x.id === id);
            if (!item) return false;
            const name = String(item.name || '').toLowerCase();
            const cat = String(item.category || '').toLowerCase();
            const hasPlov = name.includes('плов') || cat.includes('плов');
            if (!hasPlov) return false;
            const sizeHint = name.includes('1кг') || name.includes('1 кг') || name.includes('1000')
                || cat.includes('1кг') || cat.includes('1 кг') || cat.includes('1000');
            return sizeHint;
        },

        async packagingPreviewRun() {
            this.initPackagingPreviewDefaults();
            if (this.packagingPreviewMenuItemId == null) {
                void this.showUiAlert('Сначала загрузите меню и выберите блюдо.', 'Подсказка');
                return;
            }
            this.packagingPreviewLoading = true;
            this.packagingPreviewResult = null;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/packaging-rules/preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        menu_item_id: Number(this.packagingPreviewMenuItemId),
                        quantity: Number(this.packagingPreviewQty || 1),
                        order_type: String(this.packagingPreviewOrderType || 'delivery'),
                        packaging_plov_1kg: String(this.packagingPreviewPlovChoice || ''),
                    }),
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data), 'Ошибка');
                    return;
                }
                this.packagingPreviewResult = data;
            } catch (e) {
                adminLogger.error('[admin] packagingPreviewRun', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.packagingPreviewLoading = false;
            }
        },

        async loadPaymentConfigs(force = false) {
            if (this.paymentConfigsLoaded && !force) return;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/payment-config');
                if (!ok) return;
                const map = {};
                (data.items || []).forEach(item => { map[item.provider] = item; });
                this.paymentConfigs = map;
                this.paymentConfigsLoaded = true;
            } catch (e) {
                adminLogger.error('[admin] loadPaymentConfigs', e);
            }
        },

        async loadPackagingRules() {
            this.packagingLoading = true;
            try {
                const { data } = await this.apiJsonResponse('/api/admin/packaging-rules');
                this.packagingRules = Array.isArray(data.items)
                    ? data.items.map(r => ({ ...r, _saving: false, _packSaveDebounce: null }))
                    : [];
            } catch (e) {
                adminLogger.error('[admin] loadPackagingRules', e);
                this.packagingRules = [];
            } finally {
                this.packagingLoading = false;
                this.initPackagingPreviewDefaults();
            }
        },
        packagingSave(rule) {
            if (rule._packSaveDebounce) clearTimeout(rule._packSaveDebounce);
            rule._packSaveDebounce = setTimeout(() => {
                rule._packSaveDebounce = null;
                this._packagingSaveExecute(rule);
            }, 400);
        },
        async _packagingSaveExecute(rule) {
            if (rule._saving) return;
            rule._saving = true;
            try {
                const { ok, data: d } = await this.apiJsonResponse(`/api/admin/packaging-rules/${rule.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        kind: rule.kind, name: rule.name, price: rule.price,
                        iiko_product_id: rule.iiko_product_id || '',
                        keywords: rule.keywords || '', option_key: rule.option_key || '',
                        scope: rule.scope || 'item', category_match: rule.category_match || '',
                        is_active: rule.is_active, sort_order: rule.sort_order,
                    }),
                });
                if (!ok) void this.showUiAlert(this.formatApiError(d), 'Ошибка');
            } catch (e) { adminLogger.error('[admin] packagingSave', e); }
            finally { rule._saving = false; }
        },
        async packagingDelete(rule) {
            const { ok } = await this.openUiConfirm({
                message: `Удалить правило «${rule.name}»?`,
                danger: true,
            });
            if (!ok) return;
            rule._saving = true;
            try {
                const { ok, data: d } = await this.apiJsonResponse(`/api/admin/packaging-rules/${rule.id}`, { method: 'DELETE' });
                if (ok) this.packagingRules = this.packagingRules.filter(r => r.id !== rule.id);
                else void this.showUiAlert(this.formatApiError(d), 'Ошибка');
            } catch (e) { adminLogger.error('[admin] packagingDelete', e); }
            finally { rule._saving = false; }
        },
        async packagingAddNew() {
            this.packagingCreateForm = defaultPackagingRuleForm();
            this.packagingCreateError = '';
            this.packagingCreateOpen = true;
        },

        closePackagingCreateModal() {
            if (this.packagingCreateLoading) return;
            this.packagingCreateOpen = false;
            this.packagingCreateError = '';
        },

        async submitPackagingCreate() {
            const f = this.packagingCreateForm || defaultPackagingRuleForm();
            const kind = String(f.kind || '').trim();
            const name = String(f.name || '').trim();
            if (!kind) {
                this.packagingCreateError = 'Укажите уникальный ключ kind.';
                return;
            }
            if (!name) {
                this.packagingCreateError = 'Укажите понятное название правила.';
                return;
            }
            this.packagingCreateLoading = true;
            this.packagingCreateError = '';
            try {
                const { ok, data: d } = await this.apiJsonResponse('/api/admin/packaging-rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        kind,
                        name,
                        price: Number(f.price) || 0,
                        keywords: String(f.keywords || '').trim(),
                        option_key: String(f.option_key || '').trim(),
                        scope: String(f.scope || 'item').trim(),
                        category_match: String(f.category_match || '').trim(),
                        iiko_product_id: String(f.iiko_product_id || '').trim(),
                        is_active: !!f.is_active,
                        sort_order: Number(f.sort_order) || 0,
                    }),
                });
                if (!ok) {
                    this.packagingCreateError = this.formatApiError(d) || 'Не удалось создать правило';
                    return;
                }
                this.packagingCreateOpen = false;
                await this.loadPackagingRules();
            } catch (e) {
                adminLogger.error('[admin] submitPackagingCreate', e);
                this.packagingCreateError = 'Ошибка сети. Проверьте соединение.';
            } finally {
                this.packagingCreateLoading = false;
            }
        },

        async loadSetupStatus() {
            try {
                const r = await this.apiJsonResponse('/api/admin/setup-status');
                if (r.ok && r.data) {
                    this.setupStatus = {
                        score: Number(r.data.score) || 0,
                        steps: Array.isArray(r.data.steps) ? r.data.steps : [],
                        menu_items: Number(r.data.menu_items) || 0,
                        upsell_rules: Number(r.data.upsell_rules) || 0,
                        packaging_rules: Number(r.data.packaging_rules) || 0,
                        knowledge_items: Number(r.data.knowledge_items) || 0,
                    };
                    const sc = Number(this.setupStatus.score ?? 0);
                    if (sc >= 60) this.setupProgressExpanded = false;
                    else if (sc <= 30) this.setupProgressExpanded = true;
                    if (sc >= 100 && localStorage.getItem('restomind_setup_done_toast') !== '1') {
                        localStorage.setItem('restomind_setup_done_toast', '1');
                        void this.showUiAlert('Готово: филиал полностью настроен.', 'Готово');
                    }
                }
            } catch { /* ignore */ }
        },

        async loadIntegrationStatus() {
            try {
                const [st, ev] = await Promise.all([
                    this.apiJsonResponse('/api/admin/integrations/status'),
                    this.apiJsonResponse('/api/admin/integrations/events?limit=40'),
                    this.loadSetupStatus(),
                ]);
                if (st.ok && !st.notModified) this.mergeIntegrationStatus(st.data);
                if (ev.ok) this.integrationEvents = ev.data.events || [];
            } catch { /* ignore */ }
        },

        async iikoVerifyOnboard() {
            const login = (this.iikoOnboardApiLogin || '').trim();
            if (!login) {
                void this.showUiAlert('Вставьте API-ключ (apiLogin) из iiko Cloud', 'Подсказка');
                return;
            }
            this.iikoOnboardVerifyLoading = true;
            this.iikoOnboardOrgs = [];
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/integrations/iiko/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_login: login }),
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Проверка ключа не удалась', 'Ошибка');
                    return;
                }
                this.iikoOnboardOrgs = data.organizations || [];
                if (!this.iikoOnboardOrgs.length) {
                    void this.showUiAlert('Список организаций пуст — проверьте ключ', 'Внимание');
                } else if (!this.iikoOnboardSelectedOrg && this.iikoOnboardOrgs[0]) {
                    this.iikoOnboardSelectedOrg = this.iikoOnboardOrgs[0].id || '';
                }
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            } finally {
                this.iikoOnboardVerifyLoading = false;
            }
        },

        async iikoCompleteOnboard() {
            const login = (this.iikoOnboardApiLogin || '').trim();
            const orgId = (this.iikoOnboardSelectedOrg || '').trim();
            if (!login || !orgId) {
                void this.showUiAlert('Сначала проверьте ключ и выберите организацию iiko', 'Подсказка');
                return;
            }
            this.iikoOnboardSetupLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/integrations/iiko/setup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_login: login,
                        iiko_organization_id: orgId,
                        terminal_group_id: (this.iikoOnboardTerminal || '').trim(),
                    }),
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Сохранение не удалось', 'Ошибка');
                    return;
                }
                const st = data.stats || {};
                void this.showUiAlert(
                    `Меню импортировано: всего ${st.total ?? 0}, новых ${st.created ?? 0}. ` +
                        (data.encrypted ? 'Ключ сохранён зашифрованно.' : 'Задайте APP_SECRETS_FERNET_KEY для шифрования.'),
                    'Готово',
                );
                await this.loadIntegrationStatus();
                this.menuViewRevision += 1;
                await this.loadMenu();
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            } finally {
                this.iikoOnboardSetupLoading = false;
            }
        },

        async loadIikoOfficeConfig() {
            if (this.iikoOfficeLoading) return;
            this.iikoOfficeLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/iiko-office');
                if (!ok || !data) return;
                this.iikoOfficeConfig = data;
                this.iikoOfficeDraft = {
                    host: String(data.host || '').trim(),
                    login: String(data.login || '').trim(),
                    password: '',
                    store_id: String(data.store_id || '').trim(),
                    department_id: String(data.department_id || '').trim(),
                    location_id: data.location_id != null ? String(data.location_id) : '',
                };
                this.iikoOfficeDirty = false;
            } catch (_e) { /* noop */ } finally {
                this.iikoOfficeLoading = false;
            }
        },

        async saveIikoOfficeConfig() {
            if (!this.canStaffAdminOnly()) {
                void this.showUiAlert(this.staffRbacHint('admin') || 'Недостаточно прав', 'iiko Office');
                return;
            }
            if (this.iikoOfficeSaving) return;
            const host = String(this.iikoOfficeDraft?.host || '').trim();
            const login = String(this.iikoOfficeDraft?.login || '').trim();
            const storeId = String(this.iikoOfficeDraft?.store_id || '').trim();
            if (!host || !login || !storeId) {
                void this.showUiAlert('Заполните хост, логин и store_id склада iiko Office.', 'Подсказка');
                return;
            }
            this.iikoOfficeSaving = true;
            try {
                const body = {
                    host,
                    login,
                    store_id: storeId,
                    department_id: String(this.iikoOfficeDraft?.department_id || '').trim(),
                };
                const pwd = String(this.iikoOfficeDraft?.password || '').trim();
                if (pwd) body.password = pwd;
                const locRaw = String(this.iikoOfficeDraft?.location_id || '').trim();
                if (locRaw) body.location_id = Number(locRaw);
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/organization/iiko-office', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!ok) {
                    if (status === 403) {
                        void this.showUiAlert(
                            this.formatApiError(data?.detail) || 'Только администратор может менять iiko Office',
                            'iiko Office',
                        );
                    } else {
                        void this.showUiAlert(this.formatApiError(data?.detail) || 'Не удалось сохранить iiko Office', 'Ошибка');
                    }
                    return;
                }
                this.iikoOfficeConfig = data;
                this.iikoOfficeDraft.password = '';
                this.iikoOfficeDirty = false;
                void this.showUiAlert('Настройки iiko Office сохранены.', 'SupplyMind');
                if (this.currentTab === 'ai_center' && this.aiCenterTab === 'final_mile') {
                    await this.loadInventorySyncStatus();
                }
            } catch (e) {
                adminLogger.error('[admin] saveIikoOfficeConfig', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.iikoOfficeSaving = false;
            }
        },

        async loadUpsellRules() {
            this.upsellLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/upsell-rules');
                this.upsellRules = ok && Array.isArray(data.items) ? data.items : [];
            } catch (e) {
                adminLogger.error('[admin] loadUpsellRules', e);
                this.upsellRules = [];
            } finally {
                this.upsellLoading = false;
            }
        },

        async upsellAddRule() {
            const t = this.upsellNew;
            const trig = (t.trigger_category || '').trim();
            const sug = (t.suggest_category || '').trim();
            if (!trig || !sug) {
                void this.showUiAlert('Укажите категорию-триггер и категорию предложения', 'Подсказка');
                return;
            }
            const mx = t.max_order_sum;
            const body = {
                trigger_mode: 'missing_category',
                trigger_category: trig,
                suggest_category: sug,
                min_order_sum: Number(t.min_order_sum) || 0,
                max_order_sum: mx === '' || mx === null || mx === undefined ? null : Number(mx),
                phrase_template: (t.phrase_template || '').trim(),
                sort_order: Number(t.sort_order) || 0,
                is_active: true,
            };
            try {
                const { ok, data: d } = await this.apiJsonResponse('/api/admin/upsell-rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (ok) {
                    await this.loadUpsellRules();
                    await this.loadSetupStatus();
                } else void this.showUiAlert(this.formatApiError(d.detail || d), 'Ошибка');
            } catch (e) {
                adminLogger.error('[admin] upsellAddRule', e);
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            }
        },

        async upsellToggle(rule) {
            try {
                const { ok, data: d } = await this.apiJsonResponse(`/api/admin/upsell-rules/${rule.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ is_active: !rule.is_active }),
                });
                if (ok) {
                    rule.is_active = !rule.is_active;
                    await this.loadSetupStatus();
                } else void this.showUiAlert(this.formatApiError(d), 'Ошибка');
            } catch (e) { adminLogger.error(e); }
        },

        async upsellDeleteRule(rule) {
            const { ok } = await this.openUiConfirm({
                message: `Удалить правило #${rule.id}?`,
                danger: true,
            });
            if (!ok) return;
            try {
                const { ok: delOk, data: d } = await this.apiJsonResponse(`/api/admin/upsell-rules/${rule.id}`, { method: 'DELETE' });
                if (delOk) {
                    this.upsellRules = this.upsellRules.filter(r => r.id !== rule.id);
                    await this.loadSetupStatus();
                } else void this.showUiAlert(this.formatApiError(d), 'Ошибка');
            } catch (e) { adminLogger.error(e); }
        },

        async syncIntegrationsNow() {
            if (!this.integrationStatus.iiko_configured) return;
            this.integrationSyncLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/integrations/sync', { method: 'POST' });
                if (!ok) {
                    adminLogger.error('[admin] POST /integrations/sync', status, data);
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Синхронизация не удалась', 'Ошибка');
                    return;
                }
                if (data.status) {
                    this.mergeIntegrationStatus(data.status);
                } else {
                    adminLogger.warn('[admin] ответ без status, подгружаем статус');
                    await this.loadIntegrationStatus();
                }
                const ev = await this.apiJsonResponse('/api/admin/integrations/events?limit=40');
                if (ev.ok) this.integrationEvents = ev.data.events || [];
                if (data.mode === 'background' || data.menu?.pending === true) {
                    adminLogger.info('[admin] синхронизация iiko в фоне', data);
                    this.flashToast(
                        'Синхронизация iiko запущена в фоне. Статус и журнал обновятся через несколько секунд.',
                        'info',
                        6500,
                    );
                    setTimeout(async () => {
                        try {
                            await this.loadIntegrationStatus();
                            const ev2 = await this.apiJsonResponse('/api/admin/integrations/events?limit=40');
                            if (ev2.ok) this.integrationEvents = ev2.data.events || [];
                            this.menuViewRevision += 1;
                            await this.loadMenu();
                            if (this.currentTab === 'menu' && this.menuView === 'stoplist') await this.loadStopList();
                            await this.loadSetupStatus();
                        } catch (e) {
                            adminLogger.error('[admin] отложенное обновление после sync', e);
                        }
                    }, 5000);
                } else {
                    const mOk = data.menu && data.menu.ok;
                    const sOk = data.stop_lists && data.stop_lists.ok;
                    adminLogger.info('[admin] синхронизация iiko', { menu: data.menu, stop_lists: data.stop_lists });
                    if (mOk && sOk) {
                        this.flashToast('Меню и стоп-листы обновлены из iiko', 'success', 5500);
                    } else if (mOk && !sOk) {
                        this.flashToast('Меню обновлено; стоп-листы: ошибка (см. журнал ниже)', 'warning', 5500);
                    } else if (!mOk && sOk) {
                        this.flashToast('Стоп-листы обновлены; меню: ошибка (см. журнал)', 'warning', 5500);
                    } else {
                        this.flashToast('Синхронизация завершена с предупреждениями — см. журнал', 'warning', 5500);
                    }
                }
                this.menuViewRevision += 1;
                await this.loadMenu();
                if (this.currentTab === 'menu' && this.menuView === 'stoplist') await this.loadStopList();
                await this.loadSetupStatus();
            } catch (e) {
                adminLogger.error('[admin] integrations/sync', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.integrationSyncLoading = false;
            }
        },

        /** Только стоп-лист iiko → БД (учётные данные из .env). Доступна из шапки и вкладки «Стоп-лист». */
        async syncStopListOnly() {
            if (!this.integrationStatus.iiko_configured) {
                void this.showUiAlert('Настройте iiko для филиала или задайте IIKO_* в .env', 'Подсказка');
                return;
            }
            this.stopListSyncLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/stop-lists/sync', { method: 'POST' });
                if (!ok) {
                    adminLogger.error('[admin] POST /stop-lists/sync', status, data);
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Синхронизация стоп-листа не удалась', 'Ошибка');
                    return;
                }
                if (data.integration_status) {
                    this.mergeIntegrationStatus(data.integration_status);
                } else {
                    await this.loadIntegrationStatus();
                }
                const ev = await this.apiJsonResponse('/api/admin/integrations/events?limit=40');
                if (ev.ok) this.integrationEvents = ev.data.events || [];
                const st = data.stopped != null ? data.stopped : 0;
                const rs = data.restored != null ? data.restored : 0;
                adminLogger.info('[admin] стоп-лист iiko', { stopped: st, restored: rs });
                this.flashToast(`Стоп-лист iiko: в стоп ${st}, восстановлено ${rs}`, 'success', 5000);
                this.menuViewRevision += 1;
                await this.loadMenu();
                if (this.currentTab === 'menu' && this.menuView === 'stoplist') await this.loadStopList();
            } catch (e) {
                adminLogger.error('[admin] stop-lists/sync', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.stopListSyncLoading = false;
            }
        },

        async loadStopList() {
            this.stopListLoadError = '';
            try {
                // Стоп-лист строим из того же источника, что и «Меню»: полный список (available_only=false).
                // Так мы не зависим от отдельной серверной ветки stopped_only=true, которая может “пустеть”
                // из-за tenant/session edge-case'ов после деплоя.
                const { ok, data } = await this.apiJsonResponse('/api/admin/menu?available_only=false');
                if (!ok) {
                    this.stopListItems = [];
                    this.stopListFilteredItems = [];
                    this.stopListLoadError = this.formatApiError(data.detail) || 'Не удалось загрузить стоп-лист';
                    return;
                }
                const fullMenu = Array.isArray(data.items) ? data.items : [];
                // Синхронизируем локальный кэш меню: вкладка «Стоп-лист» должна быть консистентной с «Меню».
                this.menuItems = fullMenu;
                this.menuViewRevision += 1;

                // В JS лучше считать стопом любое "не доступно": false или 0.
                const derived = fullMenu.filter((i) => i && !i.is_available);
                this.stopListItems = derived;
                this._recalcStopListFiltered();
            } catch (e) {
                adminLogger.error('[admin] loadStopList', e);
                this.stopListItems = [];
                this.stopListFilteredItems = [];
                this.stopListLoadError = 'Ошибка сети';
            }
        },

        async syncMenuOnlyFromEnv() {
            if (!this.integrationStatus.iiko_configured) return;
            this.integrationSyncLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/menu/sync', { method: 'POST' });
                if (ok && data.ok) {
                    await this.loadIntegrationStatus();
                    const sk = data.skipped != null ? `, пропущено ${data.skipped}` : '';
                    this.flashToast(`Меню из iiko: новых ${data.created ?? 0}, обновлено ${data.updated ?? 0}${sk}`, 'success', 5000);
                    this.menuViewRevision += 1;
                    await this.loadMenu();
                } else {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Синхронизация меню не удалась', 'Ошибка');
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.integrationSyncLoading = false;
            }
        },

        async seedDemoData() {
            this.demoActionLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/demo/seed', { method: 'POST' });
                if (ok) {
                    if (data.partial && data.message) {
                        this.flashToast(data.message, 'warning', 5000);
                    } else {
                        const m = data.menu_items_added ? `Меню: +${data.menu_items_added} поз.` : '';
                        const u = data.users_created != null ? `Пользователей: ${data.users_created}. ` : '';
                        const o = data.orders_added != null ? `Заказов: ${data.orders_added}. ` : '';
                        this.flashToast((u + o + m).trim() || 'Демо-данные загружены', 'success', 5000);
                    }
                    await this.refreshDemoStatus();
                    try {
                        await this.loadTabData();
                    } catch (e) {
                        adminLogger.error('loadTabData после демо', e);
                    }
                } else {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось загрузить демо', 'Ошибка');
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.demoActionLoading = false;
            }
        },

        openDemoDeleteModal() {
            this.demoDeleteAck = false;
            this.demoDeleteError = '';
            this.showDemoDeleteModal = true;
        },

        closeDemoDeleteModal() {
            if (this.demoActionLoading) return;
            this.showDemoDeleteModal = false;
            this.demoDeleteError = '';
        },

        async confirmDemoDelete() {
            if (!this.demoDeleteAck) return;
            this.demoDeleteError = '';
            this.demoActionLoading = true;
            try {
                // POST вместо DELETE: часть прокси/CDN отбрасывает DELETE — «Удалить демо» тогда «не работает».
                const { ok, data } = await this.apiJsonResponse('/api/admin/demo/delete', { method: 'POST' });
                if (ok) {
                    const u = data.users_deleted ?? 0;
                    const parts = [];
                    if (u) parts.push(`пользователей: ${u}`);
                    if (data.orders_deleted) parts.push(`заказов: ${data.orders_deleted}`);
                    if (data.bookings_deleted) parts.push(`броней: ${data.bookings_deleted}`);
                    this.flashToast(
                        parts.length ? `Демо удалено (${parts.join(', ')})` : 'Демо-данные удалены',
                        'success',
                        4500,
                    );
                    this.showDemoDeleteModal = false;
                    await this.refreshDemoStatus();
                    await this.loadTabData();
                } else {
                    const msg = this.formatApiError(data.detail);
                    this.demoDeleteError = msg || 'Не удалось удалить демо-данные';
                }
            } catch {
                this.demoDeleteError = 'Ошибка сети. Проверьте соединение и попробуйте снова.';
            } finally {
                this.demoActionLoading = false;
            }
        },

        _clearWsReadyTimer() {
            if (this._wsReadyTimer) {
                clearTimeout(this._wsReadyTimer);
                this._wsReadyTimer = null;
            }
        },

        /** Цвет точки: серый (не вошли), жёлтый (коннект/ожидание ws_ready), зелёный (канал готов), красный (нет сокета). */
        wsIndicatorClass() {
            void this.wsEpoch;
            if (!this.authenticated) return 'bg-gray-400';
            const w = this.ws;
            const rs = w?.readyState;
            if (w == null || typeof rs !== 'number') return 'bg-red-400';
            if (rs === WebSocket.CONNECTING) return 'bg-amber-400 animate-pulse';
            if (rs === WebSocket.OPEN) {
                return this.wsChannelReady ? 'bg-green-400' : 'bg-amber-400 animate-pulse';
            }
            if (rs === WebSocket.CLOSING) return 'bg-amber-500 animate-pulse';
            return 'bg-red-400';
        },

        wsIndicatorText() {
            void this.wsEpoch;
            if (!this.authenticated) return 'Войдите, чтобы видеть обновления';
            const w = this.ws;
            const rs = w?.readyState;
            if (w == null || typeof rs !== 'number') return 'Связь: нет (переподключаемся…)';
            if (rs === WebSocket.CONNECTING) return 'Связь: подключаемся…';
            if (rs === WebSocket.OPEN) {
                if (this.wsChannelReady) return 'Связь: работает';
                return 'Связь: синхронизация…';
            }
            return 'Связь: переподключение…';
        },
    };
}

/** WebSocket, события, алерты, openAlertChat */
function adminMixinWebSocketEvents() {
    return {
        // ─── WebSocket ────────────────────────────────
        connectWebSocket() {
            if (!this.authenticated || !this.wsToken) return;
            this._clearWsReadyTimer();
            this.wsChannelReady = false;
            if (this._wsReconnectTimer) {
                clearTimeout(this._wsReconnectTimer);
                this._wsReconnectTimer = null;
            }
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            const url = `${proto}://${location.host}/api/admin/ws?token=${encodeURIComponent(this.wsToken)}`;

            // Не держим два сокета одновременно — иначе события (order_updated/new_message) будут дублироваться.
            // Но и не "стреляем себе в ногу": если уже есть CONNECTING/OPEN на тот же токен,
            // повторный вызов connectWebSocket() не должен закрывать рукопожатие (Edge/Chrome
            // показывают "WebSocket is closed before the connection is established").
            try {
                const rs0 = this.ws?.readyState;
                const sameToken = (this._wsTokenInUse && this._wsTokenInUse === this.wsToken);
                if (sameToken && (rs0 === WebSocket.OPEN || rs0 === WebSocket.CONNECTING)) {
                    return;
                }
                if (rs0 === WebSocket.OPEN || rs0 === WebSocket.CONNECTING) {
                    this.ws.onopen = null;
                    this.ws.onclose = null;
                    this.ws.onerror = null;
                    this.ws.onmessage = null;
                    this.ws.close();
                }
            } catch (_e) { /* noop */ }
            this._wsTokenInUse = this.wsToken;

            try {
                this.ws = new WebSocket(url);
            } catch {
                this.wsEpoch++;
                return this.scheduleReconnect();
            }
            this.wsEpoch++;

            this.ws.onopen = () => {
                this.wsReconnectDelay = 1000;
                this.wsChannelReady = false;
                this.wsEpoch++;
                this._clearWsReadyTimer();
                adminLogger.debug('[WS] Socket open, ждём ws_ready…');
            };

            this.ws.onclose = (ev) => {
                this._clearWsReadyTimer();
                this.wsChannelReady = false;
                this.ws = null;
                this.wsEpoch++;

                const code = ev && typeof ev.code === 'number' ? ev.code : null;
                const reason = ev && typeof ev.reason === 'string' ? ev.reason : '';
                if (code != null) {
                    adminLogger.warn(`[WS] Disconnected code=${code}${reason ? ` reason="${reason}"` : ''}`);
                } else {
                    adminLogger.debug('[WS] Disconnected');
                }

                // Если ошибка авторизации (4003) — токен протух, нужно обновить через checkSession
                if (code === 4003) {
                    adminLogger.info('[WS] Unauthorized (4003), refreshing token via checkSession...');
                    this.checkSession().then(() => {
                        this.scheduleReconnect();
                    });
                    return;
                }

                this.scheduleReconnect();
            };

            this.ws.onerror = () => {
                this.wsChannelReady = false;
                this.wsEpoch++;
            };

            this.ws.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    this.handleWsEvent(msg);
                } catch (e) { adminLogger.error('[WS] Parse error:', e); }
            };
        },

        scheduleReconnect() {
            if (this._wsReconnectTimer) {
                clearTimeout(this._wsReconnectTimer);
            }
            this._wsReconnectTimer = setTimeout(() => {
                this._wsReconnectTimer = null;
                if (!this.authenticated) return;
                this.wsReconnectDelay = Math.min(this.wsReconnectDelay * 1.5, 15000);
                this.connectWebSocket();
            }, this.wsReconnectDelay);
        },

        _pushDashLiveFeed(type, text) {
            const row = { type, text, ts: new Date().toISOString() };
            this.dashLiveFeed.unshift(row);
            if (this.dashLiveFeed.length > 40) {
                this.dashLiveFeed.length = 40;
            }
        },

        handleWsEvent(msg) {
            const { type, data } = msg;

            if (type === 'ws_ready') {
                this._clearWsReadyTimer();
                this.wsChannelReady = true;
                this.wsEpoch++;
                // После реконнекта могли потеряться события. Догружаем состояние по REST.
                if (this.currentTab === 'orders') {
                    void this.loadOrders();
                }
                if (this.currentTab === 'orders' || this.currentTab === 'dashboard') {
                    void this.loadDashStats();
                }
                if (this.currentTab === 'chats') {
                    void this.loadChatList();
                    if (this.activeChatPhone) void this.selectChat(this.activeChatPhone);
                }
                return;
            }

            if (type === 'message_status_updated') {
                this.onMessageStatusUpdated(data);
                const st = String(data.delivery_status || '');
                this._pushDashLiveFeed(
                    type,
                    `${st === 'failed' ? 'Сбой доставки' : 'Статус'} · ${data.phone || ''} · #${data.chat_log_id || ''}`,
                );
                if (this.currentTab === 'dashboard' && this.dashboardTab === 'overview') {
                    void this.loadDashActivity();
                }
            } else if (type === 'new_message') {
                this.onNewMessage(data);
                this._pushDashLiveFeed(
                    type,
                    `${data.role === 'user' ? 'Клиент' : (data.role === 'operator' ? 'Оператор' : 'Бот')} · ${data.phone || ''}`,
                );
                if (this.currentTab === 'dashboard' && this.dashboardTab === 'overview') {
                    // Редко, но полезно: лента событий должна быть “живой”.
                    void this.loadDashActivity();
                }
            } else if (type === 'order_updated') {
                this.onOrderUpdated(data);
                this.scheduleDashStatsRefreshDebounced();
                this._pushDashLiveFeed(
                    type,
                    `Заказ #${data.order_id} → ${data.status} · ${data.phone || ''}`,
                );
                if (this.currentTab === 'dashboard' && this.dashboardTab === 'overview') {
                    void this.loadDashActivity();
                }
            } else if (type === 'order_deleted') {
                const i = this.orders.findIndex((o) => o.id === data.order_id);
                if (i >= 0) {
                    this.orders.splice(i, 1);
                }
                const si = this.settingsOrdersList.findIndex((o) => o.id === data.order_id);
                if (si >= 0) {
                    this.settingsOrdersList.splice(si, 1);
                }
                this.settingsSelectedOrderIds = this.settingsSelectedOrderIds.filter(
                    (id) => Number(id) !== data.order_id,
                );
                if (this.selectedOrder && this.selectedOrder.id === data.order_id) {
                    this.showOrderModal = false;
                    this.selectedOrder = null;
                }
                this.scheduleDashStatsRefreshDebounced();
                if (this.currentTab === 'dashboard' && this.dashboardTab === 'overview') {
                    void this.loadDashActivity();
                }
            } else if (type === 'human_needed') {
                this.onHumanNeeded(data);
                this._pushDashLiveFeed(type, `Нужен оператор · ${data.phone || ''}`);
                if (this.currentTab === 'dashboard' && this.dashboardTab === 'overview') {
                    void this.loadDashActivity();
                }
            } else if (type === 'state_changed') {
                this.onStateChanged(data);
            } else if (type === 'bot_sla_status') {
                this.onBotSlaStatus(data);
            } else if (type === 'stoplist_updated') {
                // iiko обновил стоп-лист → перезагрузить меню если вкладка открыта
                this._pushDashLiveFeed(type, `Стоп-лист обновлён (iiko)${data.ok === false ? ' ⚠️ ошибка' : ''}`);
                if (this.currentTab === 'menu') {
                    void this.loadMenu?.();
                }
            } else if (type === 'menu_updated') {
                // iiko обновил меню → перезагрузить меню если вкладка открыта
                this._pushDashLiveFeed(type, `Меню обновлено (iiko)${data.ok === false ? ' ⚠️ ошибка' : ''}`);
                if (this.currentTab === 'menu') {
                    void this.loadMenu?.();
                }
            } else if (type === 'os.audit') {
                const orgId = Number(this.orgProfile?.organization_id || 0);
                if (!orgId || Number(data?.org_id) === orgId) {
                    this._prependAuditEntry(data);
                    this._pushDashLiveFeed(type, data?.title || data?.action || 'ОС');
                    this.scheduleDashStatsRefreshDebounced();
                }
            } else if (type === 'shift.focus_completed' || type === 'order.draft_recovered') {
                this._triggerOwnerImpactPulse();
                void this.loadRevenueLeak();
                if (this.currentTab === 'shift' || this.shouldPollShiftStateBadge()) {
                    void this.loadShiftState(true);
                }
            } else if (
                type === 'order.created'
                || type === 'order.confirmed'
                || type === 'order.cancelled'
                || type === 'payment.completed'
                || type === 'payment.failed'
                || type === 'payment.expired'
                || type === 'booking.created'
                || type === 'booking.confirmed'
                || type === 'booking.cancelled'
            ) {
                this.scheduleDashStatsRefreshDebounced();
                const label = this._osActionLabel(type);
                this._pushDashLiveFeed(type, label);
                if (this.currentTab === 'ai_center' && this.aiCenterTab === 'os') {
                    void this.loadOsDashboard();
                }
                if (this.currentTab === 'ai_center' && this.aiCenterTab === 'os' && !this.auditLog?.length) {
                    void this.loadAuditLog();
                }
            }
        },

        _prependAuditEntry(data) {
            if (!data || !data.action) return;
            const row = {
                id: `ws-${Date.now()}`,
                actor: data.actor || 'system',
                action: data.action,
                entity_type: data.entity_type,
                entity_id: data.entity_id,
                created_at: data.created_at || new Date().toISOString(),
                source: 'websocket',
            };
            this.auditLog = [row, ...(this.auditLog || [])].slice(0, 50);
        },

        async resendFailedChatMessage(msg) {
            if (!this.activeChatPhone || !msg || !msg.id) return;
            const st = String(msg.delivery_status || '').toLowerCase();
            if (st !== 'failed') return;
            const { ok, status, data } = await this.apiJsonResponse(
                `/api/admin/chats/${encodeURIComponent(this.activeChatPhone)}/messages/${msg.id}/resend`,
                { method: 'POST' },
            );
            if (!ok) {
                adminLogger.warn('resend', status, data);
                const detail = data && typeof data.detail === 'string' ? data.detail : '';
                void this.showUiAlert(
                    detail || `Не удалось переотправить (${status}). Проверьте WhatsApp и права.`,
                    'WhatsApp',
                );
                return;
            }
            await this.selectChat(this.activeChatPhone);
        },

        // ─── Event Handlers ──────────────────────────
        onMessageStatusUpdated(data) {
            const id = Number(data.chat_log_id);
            if (!id) return;
            const idx = this.chatMessages.findIndex((m) => Number(m.id) === id);
            if (idx < 0) return;
            const row = this.chatMessages[idx];
            // Новый объект + splice: Alpine 3 гарантированно перерисует бейдж (иначе in-place-мутация
            // иногда не тянет :class / x-text у вложенных вызовов chatDeliveryBadge).
            const next = { ...row };
            const st = (data.delivery_status != null && String(data.delivery_status) !== '')
                ? data.delivery_status
                : row.delivery_status;
            next.delivery_status = st;
            if (data.provider_message_id) next.provider_message_id = data.provider_message_id;
            if (data.error_details !== undefined) next.error_details = data.error_details;
            next.status_updated_at = new Date().toISOString();
            this.chatMessages.splice(idx, 1, next);
        },

        /**
         * Иконки как в мессенджерах: часы → одна галочка (сервер/облако) → две (устройство) →
         * две (прочитано, стиль в chatDeliveryBadge).
         */
        chatDeliveryMark(msg) {
            const s = String(msg.delivery_status || '').toLowerCase();
            if (!s || msg.role === 'user') return '';
            if (s === 'sending') return '\u{1F550}';
            if (s === 'sent') return '\u2713';
            if (s === 'delivered' || s === 'read') return '\u2713\u2713';
            if (s === 'failed') return '\u26A0';
            return '';
        },

        chatDeliveryTitle(msg) {
            const s = String(msg.delivery_status || '').toLowerCase();
            if (s === 'failed' && msg.error_details) {
                try {
                    return JSON.stringify(msg.error_details).slice(0, 420);
                } catch (_) {
                    return 'Ошибка доставки WhatsApp';
                }
            }
            const labels = {
                sending: 'Отправка…',
                sent: 'Доставлено в облако (сервер WhatsApp принял)',
                delivered: 'Доставлено на устройство гостя',
                read: 'Прочитано гостем',
                failed: 'Не доставлено',
            };
            return labels[s] || '';
        },

        chatDeliveryBadge(msg) {
            const s = String(msg.delivery_status || '').toLowerCase();
            if (!s || msg.role === 'user') return null;
            const label = this.chatDeliveryTitle(msg);
            const icon = this.chatDeliveryMark(msg);
            let cls = 'bg-white/15 text-white border-white/20';
            if (s === 'sending') cls = 'bg-amber-400/25 text-white border-amber-200/40 animate-pulse';
            else if (s === 'sent') cls = 'bg-slate-500/30 text-white border-slate-200/30';
            else if (s === 'delivered') cls = 'bg-emerald-500/20 text-white border-emerald-200/30';
            else if (s === 'read') cls = 'bg-sky-500/25 text-sky-50 border-sky-200/50';
            else if (s === 'failed') cls = 'bg-rose-500/20 text-white border-rose-200/30';
            return { s, label, icon, cls };
        },

        /** Текст в ленте чата: служебные [OPERATOR_ONLY] не показываем сырыми. */
        chatIsSystemMessage(msg) {
            if (!msg) return false;
            const raw = String(msg?.content || '');
            if (msg?.meta?.operator_only || /^\[OPERATOR_ONLY/i.test(raw)) return true;
            if (msg?.role === 'assistant' && msg?.meta?.technical_fallback) return true;
            return false;
        },

        chatBubbleClass(msg) {
            if (msg?.role === 'operator') {
                return 'bg-amber-500 text-white shadow-amber-500/25';
            }
            if (this.chatIsSystemMessage(msg)) {
                return 'ds-chat-bubble ds-chat-bubble--system bg-slate-100 border border-slate-200 text-slate-800 shadow-sm';
            }
            if (msg?.role === 'assistant') {
                return 'ds-chat-bubble ds-chat-bubble--ai bg-violet-600 text-white shadow-violet-600/20';
            }
            return 'bg-brand-600 text-white shadow-brand-600/25';
        },

        chatHasRecentFallback() {
            const msgs = Array.isArray(this.chatMessages) ? this.chatMessages : [];
            return msgs.slice(-8).some((m) => this.chatIsSystemMessage(m));
        },

        formatChatDisplayContent(msg) {
            if (msg?._collapsed_system && Number(msg._collapsed_count || 0) > 1) {
                const n = Number(msg._collapsed_count);
                const word = n === 2 ? 'дважды' : n === 3 ? 'трижды' : `${n} раз`;
                return `ИИ ${word} не смог ответить · передано оператору`;
            }
            const raw = String(msg?.content || '');
            if (msg?.meta?.operator_only || /^\[OPERATOR_ONLY/i.test(raw)) {
                return 'ИИ не отвечает (ожидает оператора)';
            }
            if (msg?.role === 'assistant' && msg?.meta?.technical_fallback) {
                return 'ИИ не смог ответить · передано оператору';
            }
            return raw;
        },

        chatTechnicalFallbackBadge(msg) {
            if (!this.chatIsSystemMessage(msg)) return null;
            if (msg?.role !== 'assistant' && !(msg?.meta?.operator_only || /^\[OPERATOR_ONLY/i.test(String(msg?.content || '')))) {
                return null;
            }
            return {
                label: msg?._collapsed_system && Number(msg._collapsed_count || 0) > 1 ? 'Сбой ИИ ×' + msg._collapsed_count : 'Сбой ИИ',
                cls: 'bg-rose-100 text-rose-800 border-rose-200',
            };
        },

        /** Один статус в шапке чата (без конкурирующих бейджей). */
        chatModeSummary() {
            if (this.customerAiSnoozeActive()) {
                return {
                    label: 'ИИ на паузе',
                    subline: `до ${this.customerAiSnoozeUntilLabel()}`,
                    tone: 'pause',
                };
            }
            if (this.activeChatState === 'confirming_order') {
                return {
                    label: 'Подтверждение заказа',
                    subline: 'Гость подтверждает состав',
                    tone: 'confirm',
                };
            }
            if (this.activeChatState === 'human_mode' || this.customerSummary?.ai_paused) {
                const esc = this.customerSummary?.last_escalation;
                return {
                    label: 'Отвечаете вы',
                    subline: esc ? 'Диалог передан от бота' : null,
                    tone: 'you',
                };
            }
            return {
                label: 'Отвечает ИИ',
                subline: null,
                tone: 'ai',
            };
        },

        chatModeToneClass() {
            const tone = this.chatModeSummary().tone;
            if (tone === 'you') return 'bg-orange-50 text-orange-800 border-orange-100';
            if (tone === 'pause') return 'bg-violet-50 text-violet-900 border-violet-100';
            if (tone === 'confirm') return 'bg-amber-50 text-amber-900 border-amber-100';
            return 'bg-emerald-50 text-emerald-800 border-emerald-100';
        },

        chatOperatorPlaceholder() {
            if (this.chatOperatorInputDisabled()) {
                return 'Чтобы написать гостю, нажмите «Ответить самому»…';
            }
            return 'Сообщение гостю (Enter — отправить, Shift+Enter — новая строка)…';
        },

        chatOperatorInputDisabled() {
            if (this.activeChatState === 'human_mode') return false;
            if (this.customerSummary?.user_exists && this.customerSummary.ai_paused) return false;
            if (this.customerSummary?.user_exists && this.customerAiSnoozeActive()) return false;
            if (this.chatHasRecentFallback()) return false;
            return true;
        },

        chatListStateLabel(state) {
            if (state === 'human_mode') return 'Вы';
            if (state === 'confirming_order') return 'Заказ';
            return 'ИИ';
        },

        chatWaitSeconds(chat) {
            void this._chatPulseAt;
            const role = String(chat?.lastRole || '').toLowerCase();
            if (role !== 'user') return 0;
            const raw = chat?.lastAt;
            if (!raw) return Number(chat?.waitSeconds || 0);
            const ts = Date.parse(String(raw));
            if (!Number.isFinite(ts)) return Number(chat?.waitSeconds || 0);
            return Math.max(0, Math.floor((Date.now() - ts) / 1000));
        },

        chatPulseStatus(chat) {
            const wait = this.chatWaitSeconds(chat);
            const role = String(chat?.lastRole || '').toLowerCase();
            if (role === 'user') {
                if (wait >= 300) return 'red';
                if (wait >= 120) return 'amber';
                return 'green';
            }
            if (chat?.chatSlow) return 'red';
            return String(chat?.pulse || chat?.slaStatus || 'green');
        },

        chatSlaDotClass(chat) {
            const s = this.chatPulseStatus(chat);
            if (s === 'red') return 'bg-red-500 shadow-[0_0_0_3px_rgba(239,68,68,0.18)] animate-pulse';
            if (s === 'amber') return 'bg-amber-400 shadow-[0_0_0_3px_rgba(245,158,11,0.14)]';
            return 'bg-emerald-400';
        },

        chatSlaTitle(chat) {
            const wait = this.chatWaitSeconds(chat);
            const s = this.chatPulseStatus(chat);
            if (String(chat?.lastRole || '').toLowerCase() === 'user') {
                if (s === 'red') return `Live Pulse: клиент ждёт ${Math.floor(wait / 60)} мин — риск потери заказа`;
                if (s === 'amber') return `Live Pulse: клиент ждёт ${Math.max(1, Math.floor(wait / 60))} мин`;
                return 'Live Pulse: клиент ждёт < 2 мин';
            }
            if (s === 'red') return 'Live Pulse: чат помечен как просроченный';
            if (chat?.botShortMode) return 'Нагрузка: бот в кратком режиме';
            return 'Live Pulse: ответ дан, ожидания нет';
        },

        activeChatSlaStatus() {
            const c = this.chatList.find(x => x.phone === this.activeChatPhone);
            return c ? this.chatPulseStatus(c) : (this.botSlaStatus?.bot_short_mode ? 'amber' : 'green');
        },

        botShortModeBannerVisible() {
            return this.activeChatPhone && (
                this.activeChatSlaStatus() === 'red' ||
                !!this.botSlaStatus?.bot_short_mode
            );
        },

        onBotSlaStatus(data) {
            const eventLocationId = Number(data?.location_id || 0);
            const selectedLocationId = Number(this.selectedLocationId || 0);
            if (selectedLocationId > 0 && eventLocationId > 0 && eventLocationId !== selectedLocationId) return;
            this.botSlaStatus = {
                bot_short_mode: !!data.bot_short_mode,
                slow_chats: Number(data.slow_chats || 0),
                location_id: data.location_id ?? null,
            };
            const phone = data.phone;
            if (phone) {
                const idx = this.chatList.findIndex((c) => c.phone === phone);
                if (idx >= 0) {
                    this.chatList[idx].botShortMode = !!data.bot_short_mode;
                    this.chatList[idx].slowChats = Number(data.slow_chats || 0);
                    this.chatList[idx].chatSlow = !!data.chat_slow;
                    if (data.chat_slow) {
                        this.chatList[idx].slaStatus = 'red';
                        this.chatList[idx].pulse = 'red';
                    }
                }
            }
            for (const chat of this.chatList) {
                if (phone && chat.phone === phone) continue;
                chat.botShortMode = !!data.bot_short_mode;
                chat.slowChats = Number(data.slow_chats || 0);
            }
        },

        onNewMessage(data) {
            const eventLocationId = Number(data?.location_id || 0);
            const selectedLocationId = Number(this.selectedLocationId || 0);
            if (selectedLocationId > 0 && eventLocationId > 0 && eventLocationId !== selectedLocationId) return;
            const chatIdx = this.chatList.findIndex(c => c.phone === data.phone);
            const msgRole = String(data.role || 'user').toLowerCase();
            if (chatIdx >= 0) {
                this.chatList[chatIdx].lastMessage = data.content?.slice(0, 60) || '';
                this.chatList[chatIdx].lastAt = data.created_at || new Date().toISOString();
                this.chatList[chatIdx].lastRole = msgRole;
                this.chatList[chatIdx].waitSeconds = msgRole === 'user' ? 0 : null;
                this.chatList[chatIdx].pulse = msgRole === 'user' ? 'green' : 'green';
                this.chatList[chatIdx].slaStatus = this.chatList[chatIdx].pulse;
                if (msgRole !== 'user') this.chatList[chatIdx].chatSlow = false;
                if (data.phone !== this.activeChatPhone) {
                    this.chatList[chatIdx].unread = true;
                    this.unreadChats = this.chatList.filter(c => c.unread).length;
                }
                const item = this.chatList.splice(chatIdx, 1)[0];
                this.chatList.unshift(item);
            } else {
                this.chatList.unshift({
                    phone: data.phone,
                    lastMessage: data.content?.slice(0, 60) || '',
                    state: 'chatting',
                    unread: data.phone !== this.activeChatPhone,
                    lastAt: data.created_at || new Date().toISOString(),
                    lastRole: msgRole,
                    waitSeconds: msgRole === 'user' ? 0 : null,
                    botShortMode: !!this.botSlaStatus?.bot_short_mode,
                    slowChats: Number(this.botSlaStatus?.slow_chats || 0),
                    pulse: 'green',
                    slaStatus: 'green',
                    chatSlow: false,
                });
                this.unreadChats = this.chatList.filter(c => c.unread).length;
            }

            const newMsg = {
                id: data.id,
                role: data.role,
                content: data.content,
                created_at: data.created_at || new Date().toISOString(),
                delivery_status: data.delivery_status ?? null,
                provider_message_id: data.provider_message_id ?? null,
                error_details: data.error_details ?? null,
                status_updated_at: data.status_updated_at ?? null,
                meta: (data.meta && typeof data.meta === 'object') ? data.meta : null,
            };
            // Обновляем кэш чата, даже если он не открыт сейчас
            const cachedForMsg = this._chatCacheGet(data.phone);
            if (cachedForMsg) {
                cachedForMsg.messages.push(newMsg);
                cachedForMsg.ts = Date.now();
            }
            if (data.phone === this.activeChatPhone) {
                this.chatMessages.push(newMsg);
                this.scrollChatToBottom();
            }

            // Звук входящего от клиента (если смотрим другой чат или вкладка в фоне)
            if (data.role === 'user' && (data.phone !== this.activeChatPhone || document.hidden)) {
                this.playChatPing();
            }
        },

        /** Время для сортировки списка заказов (новые выше); только валидная дата — иначе null. */
        _orderListSortTs(o) {
            const raw = o && (o.created_at || o.updated_at || o.createdAt);
            if (raw == null || raw === '') return null;
            const t = new Date(raw).getTime();
            return Number.isFinite(t) ? t : null;
        },

        onOrderUpdated(data) {
            const oid = Number(data.order_id);
            const idx = this.orders.findIndex((o) => Number(o.id) === oid);

            if (data.order) {
                const incoming = data.order;
                const prev = idx >= 0 ? this.orders[idx] : null;
                if (prev && Number(prev.row_version || 0) > Number(incoming.row_version || 0)) {
                    return;
                }
                if (idx >= 0) this.orders.splice(idx, 1, incoming);
                else this.orders.unshift(incoming);
            } else {
                // Если событие не несёт полный заказ — синхронизируемся по REST.
                void this.loadOrders();
            }

            const sidx = this.settingsOrdersList.findIndex((o) => Number(o.id) === oid);
            if (data.order && sidx >= 0) {
                this.settingsOrdersList.splice(sidx, 1, data.order);
            }

            if (this.selectedOrder && Number(this.selectedOrder.id) === oid) {
                const fresh = this.orders.find((o) => Number(o.id) === oid);
                if (fresh) this.selectedOrder = fresh;
            }
        },

        onHumanNeeded(data) {
            const raw = (data.user_message || '').trim();
            const um = raw.slice(0, 120);
            const hint = um ? `«${um}${raw.length > 120 ? '…' : ''}»` : '';
            this.alertQueue.push({
                phone: data.phone,
                message: `Чат ${data.phone}: ${hint || (data.reason?.slice(0, 80) || 'Бот не может ответить')}`,
            });
            this.playAlertSound();
            const chatIdx = this.chatList.findIndex((c) => c.phone === data.phone);
            if (chatIdx >= 0) {
                this.chatList[chatIdx].state = 'human_mode';
            }
            if (data.phone === this.activeChatPhone) {
                this.activeChatState = 'human_mode';
            }
        },

        onStateChanged(data) {
            const chatIdx = this.chatList.findIndex(c => c.phone === data.phone);
            if (chatIdx >= 0) {
                this.chatList[chatIdx].state = data.state;
            }
            if (data.phone === this.activeChatPhone) {
                this.activeChatState = data.state;
            }
        },

        _startChatPolling() {
            this._stopChatPolling();
            // Fallback-refresh каждые 20 с, пока WS не готов или после долгого обрыва.
            this._chatPollTimer = setInterval(() => {
                if (!this.authenticated) return;
                if (!this.wsChannelReady && this.currentTab === 'chats') {
                    void this.loadChatList(false);
                }
            }, 20000);
        },

        _stopChatPolling() {
            if (this._chatPollTimer) {
                clearInterval(this._chatPollTimer);
                this._chatPollTimer = null;
            }
        },

        // ─── Alerts ──────────────────────────────────
        _playTone(type, freq, duration = 0.2, volume = 0.12) {
            try {
                const Ctx = window.AudioContext || window.webkitAudioContext;
                this._audioCtx = this._audioCtx || new Ctx();
                const ctx = this._audioCtx;
                if (ctx && ctx.state === 'suspended' && !this._audioUnlocked) return;
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = type;
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.frequency.setValueAtTime(freq, ctx.currentTime);
                gain.gain.setValueAtTime(volume, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + duration);
                osc.start();
                osc.stop(ctx.currentTime + duration);
                osc.onended = () => {
                    try { osc.disconnect(); } catch {}
                };
            } catch {}
        },

        playAlertSound() {
            this._playTone('sine', 880, 0.14, 0.18);
            setTimeout(() => this._playTone('sine', 660, 0.14, 0.16), 160);
            setTimeout(() => this._playTone('sine', 880, 0.18, 0.18), 320);
        },

        /** Короткий сигнал: новое сообщение клиента (мягче, чем «бот зовёт»). */
        playChatPing() {
            this._playTone('sine', 520, 0.12, 0.12);
        },

        /** Новый заказ в ленте канбана. */
        playOrderPing() {
            this._playTone('triangle', 392, 0.10, 0.14);
            setTimeout(() => this._playTone('triangle', 523, 0.12, 0.12), 90);
        },

        dismissAlert() {
            this.alertQueue.shift();
        },

        /**
         * Нижний тост (единые классы rm-toast-* в admin.css).
         * @param {string} message
         * @param {'success'|'warning'|'error'|'info'} [kind]
         * @param {number} [ms]
         */
        flashToast(message, kind = 'info', ms = 3500) {
            const k = ['success', 'warning', 'error', 'info'].includes(kind) ? kind : 'info';
            this.demoToastMessage = message;
            this.demoToastKind = k;
            if (this._demoToastTimer) clearTimeout(this._demoToastTimer);
            this._demoToastTimer = setTimeout(() => {
                this.demoToastMessage = '';
                this.demoToastKind = 'info';
                this._demoToastTimer = null;
            }, ms);
        },

        async openAlertChat(phone) {
            if (!phone) return;
            this.currentTab = 'chats';
            this.dismissAlert();
            await this.selectChat(phone);
            setTimeout(() => this.scrollChatToBottom(), 350);
            await this.takeoverChat();
        },

        /** Открыть диалог из «Помощь клиентам» (без влияния на alertQueue). */
        async openHelpChat(phone) {
            if (!phone) return;
            this.currentTab = 'chats';
            await this.selectChat(phone);
            setTimeout(() => this.scrollChatToBottom(), 350);
            await this.takeoverChat();
        },
    };
}

/** Focus-Driven OS — currentMode, setMode, mode↔tab helpers (Sprint 1). */
function adminMixinModeEngine() {
    return {
        /** @type {'shift'|'control'|'intelligence'} */
        currentMode: 'shift',

        get isShiftMode() {
            return this.currentMode === 'shift';
        },

        get isControlMode() {
            return this.currentMode === 'control';
        },

        get isIntelligenceMode() {
            return this.currentMode === 'intelligence';
        },

        _readStoredAdminMode() {
            try {
                return adminNormalizeAdminMode(window.localStorage?.getItem(ADMIN_MODE_STORAGE_KEY));
            } catch (_e) {
                return null;
            }
        },

        _persistAdminMode() {
            try {
                window.localStorage?.setItem(ADMIN_MODE_STORAGE_KEY, this.currentMode);
            } catch (_e) { /* ignore */ }
        },

        /**
         * Sidebar/hash navigation: sync mode to match the opened tab (Strangler).
         * Mode Bar calls setMode() explicitly; legacy sidebar stays fully functional.
         * We sync on tab change so currentMode always reflects the active screen family.
         */
        _syncModeFromTab(tabId) {
            const inferred = adminModeForTab(tabId);
            if (!inferred || inferred === this.currentMode) return;
            this.currentMode = inferred;
            this._persistAdminMode();
        },

        defaultTabForMode(mode) {
            return adminDefaultTabForMode(mode);
        },

        tabsForMode(mode) {
            return adminTabsForMode(mode);
        },

        tabsForCurrentMode() {
            return adminTabsForMode(this.currentMode);
        },

        isTabInCurrentMode(tabId) {
            return adminTabBelongsToMode(tabId, this.currentMode);
        },

        /**
         * Switch Focus-Driven OS mode; maps to allowed tabs without hiding the legacy sidebar yet.
         * @param {'shift'|'control'|'intelligence'} mode
         * @param {{ navigate?: boolean }} [opts]
         */
        setMode(mode, opts) {
            const m = adminNormalizeAdminMode(mode);
            if (!m) return;
            const o = opts && typeof opts === 'object' ? opts : {};
            const navigate = o.navigate !== false;
            const prev = this.currentMode;
            this.currentMode = m;
            this._persistAdminMode();
            if (navigate && !adminTabBelongsToMode(this.currentTab, m)) {
                this.navigateToTab(adminDefaultTabForMode(m));
            }
            if (prev !== m) {
                try {
                    window.dispatchEvent(new CustomEvent('restomind:admin-mode', {
                        detail: { mode: m, previous: prev },
                    }));
                } catch (_e) { /* ignore */ }
            }
        },

        /**
         * After auth: hash tab wins; sync internal mode from tab (role-first IA).
         * @param {{ tabFromHash?: string | null }} [ctx]
         */
        _bootstrapAdminMode(ctx) {
            const fromHash = ctx?.tabFromHash ?? null;
            if (fromHash) {
                this._syncModeFromTab(fromHash);
                return;
            }
            this._syncModeFromTab(this.currentTab);
        },
    };
}

/** Focus-Driven OS — Command Bar (Ctrl+K / Cmd+K), Sprint 4 Strangler over global search. */
function adminMixinCommandBar() {
    return {
        commandBarOpen: false,
        commandQuery: '',

        /** Strangler: header buttons and legacy openGlobalSearch → unified palette. */
        openGlobalSearch() {
            this.openCommandBar();
        },

        openCommandBar(prefill) {
            this.commandBarOpen = true;
            this.globalSearchOpen = false;
            this.commandQuery = typeof prefill === 'string' ? prefill : '';
            this.globalSearchQ = this.commandQuery;
            this.globalSearchLastFetchedQ = '';
            this.globalSearchResults = { orders: [], chats: [], bookings: [] };
            this.$nextTick(() => {
                requestAnimationFrame(() => {
                    const el = document.getElementById('command-bar-input');
                    if (el) {
                        el.focus();
                        el.select();
                    }
                });
            });
            if ((this.commandQuery || '').trim().length >= 3 && !this.parseCommand(this.commandQuery)) {
                void this.runGlobalSearch();
            }
        },

        closeCommandBar() {
            this.commandBarOpen = false;
            this.commandQuery = '';
            this.globalSearchOpen = false;
        },

        parseCommand(query) {
            return adminParseCommand(query);
        },

        commandBarSuggestions() {
            return adminCommandBarSuggestions(this.commandQuery);
        },

        commandBarDefaultCommands() {
            return [...ADMIN_COMMAND_DEFINITIONS];
        },

        commandBarShowsSearch() {
            const q = (this.commandQuery || '').trim();
            return q.length > 0 && !q.startsWith('/');
        },

        onCommandBarInput() {
            this.globalSearchQ = this.commandQuery;
            if (this.commandBarShowsSearch()) {
                void this.runGlobalSearch();
            } else {
                this.globalSearchLastFetchedQ = '';
                this.globalSearchResults = { orders: [], chats: [], bookings: [] };
            }
        },

        commandBarSubmit() {
            const cmd = this.parseCommand(this.commandQuery);
            if (cmd) {
                this.executeCommand(cmd);
                return;
            }
            const q = (this.commandQuery || '').trim();
            if (q.length >= 3) void this.runGlobalSearch();
        },

        commandBarPickSuggestion(def) {
            if (!def || !def.prefix) return;
            this.commandQuery = def.prefix;
            this.commandBarSubmit();
        },

        /**
         * Execute parsed command prefix.
         * @param {{ id: string, prefix?: string, args?: string }} cmd
         */
        executeCommand(cmd) {
            if (!cmd || !cmd.id) return;
            this.closeCommandBar();
            switch (cmd.id) {
                case 'leak':
                    this.navigateToTab('dashboard', { dashboardTab: 'overview' });
                    void this.loadRevenueLeak();
                    this.flashToast('Упущенная выручка', 'info', 2800);
                    break;
                case 'red':
                    this.navigateToTab('shift');
                    this.flashToast('Риск · смена', 'warning', 2800);
                    break;
                case 'force-close':
                    this.navigateToTab('settings', { settingsTab: 'restaurant' });
                    if (typeof this.openForceCloseModal === 'function') {
                        this.$nextTick(() => this.openForceCloseModal());
                    }
                    break;
                default:
                    break;
            }
        },

        handleCommandBarKeydown(e) {
            if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
                e.preventDefault();
                if (this.commandBarOpen) this.closeCommandBar();
                else this.openCommandBar();
            }
        },
    };
}

/** Shell v2 G10.5 — Focus Card mapper (single source for focus → UI view model). */
function adminFocusCardSemantics(focus) {
    if (!focus || typeof focus !== 'object') return 'ds-status-inactive';
    const pulse = String(focus.pulse || '');
    if (pulse === 'red') return 'ds-status-danger';
    if (pulse === 'amber') return 'ds-status-warn';
    const kind = String(focus.kind || '');
    const wait = Number(focus.wait_minutes ?? 0);
    if (kind === 'slow_chat' && wait >= 5) return 'ds-status-danger';
    if (Number(focus.value_kzt ?? 0) > 0) return 'ds-status-warn';
    return 'ds-status-ok';
}

function adminFocusCardContextRoute(focus) {
    if (!focus || typeof focus !== 'object') return null;
    const kind = String(focus.kind || '');
    const pulse = String(focus.pulse || '');
    if (kind === 'slow_chat' || kind === 'menu_confusion' || pulse === 'red' || pulse === 'amber') {
        const id = focus.phone || focus.id;
        if (id) return { type: 'chat', id: String(id) };
    }
    if (kind === 'booking_at_risk' && focus.booking_id != null) {
        return { type: 'booking', id: focus.booking_id };
    }
    if (kind === 'abandoned_draft' || kind === 'pending_prepay' || kind === 'high_value_stuck' || focus.order_id != null) {
        return { type: 'order', id: focus.order_id };
    }
    return null;
}

function adminFocusCardFromShiftState(shiftState) {
    const ss = shiftState && typeof shiftState === 'object' ? shiftState : {};
    const focus = ss.focus;
    if (!focus || !focus.id) return null;
    const compressed = ss.compressed_actions && typeof ss.compressed_actions === 'object'
        ? ss.compressed_actions
        : { primary: null, secondary: null, tertiary: null };
    return {
        id: focus.id,
        kind: focus.kind,
        title: focus.title || '',
        subtitle: focus.subtitle || '',
        risk_kzt: Number(focus.value_kzt ?? 0),
        wait_minutes: Number(focus.wait_minutes ?? 0),
        pulse: focus.pulse || '',
        semantics: adminFocusCardSemantics(focus),
        why_this_card: focus.why_this_card || '',
        ai_hint: focus.ai_hint || '',
        confidence: Number(focus.confidence ?? 0),
        actions: Array.isArray(focus.actions) ? focus.actions.slice(0, 3) : [],
        compressed_actions: compressed,
        state_actions: Array.isArray(ss.actions) ? ss.actions : [],
        context_route: adminFocusCardContextRoute(focus),
        ownership: ss.presentation?.focus_ownership || focus.ownership || 'mine',
        anticipation: focus.anticipation && typeof focus.anticipation === 'object' ? focus.anticipation : {},
        phone: focus.phone || '',
        order_id: focus.order_id != null ? focus.order_id : null,
        booking_id: focus.booking_id != null ? focus.booking_id : null,
    };
}

/** Focus-Driven OS Sprint 2 — Shift split + mobile staged nav (focus ↔ context). */
function adminMixinShiftStagedNav() {
    return {
        /** @type {'focus'|'context'} */
        mobileActiveScreen: 'focus',

        shiftFocusShowsChatDock() {
            const f = this.shiftState?.focus;
            if (!f) return false;
            const kind = String(f.kind || '');
            if (kind === 'slow_chat' || kind === 'menu_confusion') return true;
            const pulse = String(f.pulse || '');
            return pulse === 'red' || pulse === 'amber';
        },

        shiftFocusShowsOrderDock() {
            const k = String(this.shiftState?.focus?.kind || '');
            return k === 'abandoned_draft' || k === 'pending_prepay' || k === 'high_value_stuck';
        },

        shiftFocusShowsBookingDock() {
            return String(this.shiftState?.focus?.kind || '') === 'booking_at_risk';
        },

        shiftHasContextDock() {
            return this.shiftFocusShowsChatDock() || this.shiftFocusShowsOrderDock() || this.shiftFocusShowsBookingDock();
        },

        _shiftStagedNavIsDesktop() {
            try {
                return window.matchMedia('(min-width: 1024px)').matches;
            } catch (_e) {
                return false;
            }
        },

        shiftMobileShowsFocus() {
            return this._shiftStagedNavIsDesktop() || this.mobileActiveScreen === 'focus';
        },

        shiftMobileShowsContext() {
            return this._shiftStagedNavIsDesktop() || this.mobileActiveScreen === 'context';
        },

        openShiftContext() {
            if (!this.shiftHasContextDock()) return;
            this.mobileActiveScreen = 'context';
        },

        backToShiftFocus() {
            this.mobileActiveScreen = 'focus';
        },

        shiftDockPulseClass() {
            const pulse = String(this.shiftState?.focus?.pulse || '');
            if (pulse === 'red') return 'ds-status-danger';
            if (pulse === 'amber') return 'ds-status-warn';
            return 'ds-status-inactive';
        },

        shiftDockOpenChat() {
            const phone = this.shiftState?.focus?.phone;
            if (phone) void this.openHelpChat(String(phone));
        },

        shiftDockOpenOrder() {
            const oid = this.shiftState?.focus?.order_id;
            if (oid != null) this.openGuestContextOrder({ id: Number(oid) });
        },

        shiftDockRunFocusAction(action) {
            this.runShiftFocusAction(action);
        },

        shiftDockOrderLines() {
            const oid = Number(this.shiftState?.focus?.order_id);
            if (!oid) return [];
            const o = (this.orders || []).find((x) => Number(x.id) === oid);
            if (!o) return [];
            const raw = o.items?.items
                || (o.items_json && typeof o.items_json === 'object' ? o.items_json.items : null)
                || [];
            return Array.isArray(raw) ? raw.slice(0, 8) : [];
        },

        shiftDockHasOrderLines() {
            return this.shiftDockOrderLines().length > 0;
        },

        focusCardFromShiftState() {
            return adminFocusCardFromShiftState(this.shiftState);
        },

        focusCardView() {
            const card = this.focusCardFromShiftState();
            if (!card) return null;
            return {
                ...card,
                kind_label: this.moneyQueueKindLabel(card.kind),
            };
        },

        focusCardSemanticsClass() {
            const card = this.focusCardView();
            return card?.semantics || 'ds-status-inactive';
        },

        focusCardHasOwnershipConflict() {
            return String(this.focusCardView()?.ownership || '') === 'other';
        },

        shiftLiveImpactStripClass() {
            const anim = String(this.shiftState?.live_impact?.animation || '');
            if (anim === 'pulse_green') return 'ds-live-impact-strip--pulse_green';
            if (anim === 'fade_shrink') return 'ds-live-impact-strip--fade_shrink';
            return 'ds-live-impact-strip--pulse_green';
        },

        shiftLiveImpactPulseActive() {
            return !!this.shiftLiveImpactPulse;
        },

        shiftStateEscalationClass() {
            const state = String(this.shiftState?.state || '');
            if (state === 'S1' || state === 'S5') return 'ds-state-escalation-shake';
            return '';
        },

        shiftLiveImpactPayload() {
            if (this.shiftChoreoImpact) return this.shiftChoreoImpact;
            return this.shiftState?.live_impact || null;
        },

        shiftLiveImpactVisible() {
            if (this.shiftChoreoImpact) return true;
            if (this.shiftChoreoPhase === 'exiting') return false;
            return !!this.shiftState?.live_impact;
        },

        shiftLiveImpactNarrative() {
            return adminRenderLiveImpactNarrative(this.shiftLiveImpactPayload());
        },

        shiftLiveImpactPrefixLine() {
            return adminLiveImpactOutcomePrefix(this.shiftLiveImpactPayload());
        },

        shiftLiveImpactEmotionLine() {
            return adminLiveImpactOutcomeEmotion(this.shiftLiveImpactPayload());
        },

        shiftLiveImpactPrefixVisible() {
            const p = this.shiftLiveImpactPayload();
            if (!p || !adminLiveImpactUsesCompressed(p)) return false;
            const prefix = this.shiftLiveImpactPrefixLine();
            if (!prefix) return false;
            if (this.shiftChoreoPhase === 'impact') {
                return ['prefix', 'emotion', 'money'].includes(this.shiftImpactRevealPhase);
            }
            return true;
        },

        shiftLiveImpactEmotionVisible() {
            const p = this.shiftLiveImpactPayload();
            if (!p) return false;
            if (this.shiftChoreoPhase === 'impact') {
                return ['emotion', 'money'].includes(this.shiftImpactRevealPhase);
            }
            return !!this.shiftLiveImpactEmotionLine();
        },

        shiftLiveImpactMoneyVisible() {
            const p = this.shiftLiveImpactPayload();
            if (!p || !this.shiftLiveImpactMoneyLine()) return false;
            if (this.shiftChoreoPhase === 'impact') {
                return this.shiftImpactRevealPhase === 'money';
            }
            return true;
        },

        shiftLiveImpactMoneyLine() {
            return adminLiveImpactMoneyLabel(this.shiftLiveImpactPayload());
        },

        shiftLiveImpactReasonLine() {
            return adminLiveImpactReasonOnly(this.shiftLiveImpactPayload());
        },

        shiftLiveImpactIsCompressed() {
            return adminLiveImpactUsesCompressed(this.shiftLiveImpactPayload());
        },

        shiftPredictiveScene() {
            return this.shiftState?.predictive_scene && typeof this.shiftState.predictive_scene === 'object'
                ? this.shiftState.predictive_scene
                : {};
        },

        shiftPredictiveTensionVisible() {
            if (this.shiftChoreoPhase !== 'idle' && this.shiftChoreoPhase !== 'entering') return false;
            const scene = this.shiftPredictiveScene();
            if (scene.active) return true;
            const ant = this.focusCardView()?.anticipation || {};
            return !!ant.pre_attention;
        },

        shiftPredictiveAnticipationText() {
            const scene = this.shiftPredictiveScene();
            if (scene.scene_headline) return scene.scene_headline;
            return String(this.focusCardView()?.anticipation?.anticipation_text || '');
        },

        shiftPredictiveInevitabilityText() {
            const scene = this.shiftPredictiveScene();
            if (scene.inevitability) return scene.inevitability;
            return String(this.focusCardView()?.anticipation?.inevitability_text || '');
        },

        shiftPredictiveTensionClass() {
            const level = String(
                this.shiftPredictiveScene().tension_level
                || this.focusCardView()?.anticipation?.tension_level
                || 'stable',
            );
            if (level === 'imminent') return 'ds-predictive-tension--imminent';
            if (level === 'critical') return 'ds-predictive-tension--critical';
            if (level === 'rising') return 'ds-predictive-tension--rising';
            return '';
        },

        focusCardPreAttentionClass() {
            if (this.shiftChoreoPhase !== 'idle') return '';
            const ant = this.focusCardView()?.anticipation || {};
            if (!ant.pre_attention) return '';
            const level = String(ant.tension_level || 'rising');
            if (level === 'imminent') return 'ds-focus-pre-attention--imminent';
            if (level === 'critical') return 'ds-focus-pre-attention--critical';
            return 'ds-focus-pre-attention--rising';
        },

        shiftRiskTrajectoryClass() {
            const traj = String(this.shiftPredictiveScene().risk_trajectory || '');
            if (traj === 'rising') return 'ds-risk-trajectory-rising';
            return '';
        },

        shiftPreAttentionTickLabel() {
            if (!this._shiftPreAttentionTick) return '';
            return 'Риск растёт';
        },

        _stopShiftPreAttention() {
            if (this._shiftPreAttentionTimer) {
                clearInterval(this._shiftPreAttentionTimer);
                this._shiftPreAttentionTimer = null;
            }
            this._shiftPreAttentionTick = 0;
        },

        _syncShiftPreAttention() {
            this._stopShiftPreAttention();
            const card = this.focusCardView();
            if (!card || this.currentTab !== 'shift') return;
            const ant = card.anticipation || {};
            if (!ant.pre_attention) return;
            this._shiftPreAttentionTimer = setInterval(() => {
                this._shiftPreAttentionTick += 1;
            }, 12000);
        },

        async _runImpactRevealSequence(impact) {
            const ms = SHIFT_CHOREO_MS;
            this.shiftImpactRevealPhase = 'idle';
            const compressed = adminLiveImpactUsesCompressed(impact);
            if (!compressed) {
                this.shiftImpactRevealPhase = 'emotion';
                if (impact?.animation) this._triggerShiftLiveImpactPulse(impact.animation);
                await adminSleep(ms.pulseAfterImpact);
                this.shiftImpactRevealPhase = 'idle';
                return;
            }
            const prefix = adminLiveImpactOutcomePrefix(impact);
            if (prefix) {
                this.shiftImpactRevealPhase = 'prefix';
                await adminSleep(ms.impactPrefixReveal);
            }
            this.shiftImpactRevealPhase = 'emotion';
            await adminSleep(ms.impactEmotionReveal);
            const money = adminLiveImpactMoneyShort(impact);
            if (money) {
                this.shiftImpactRevealPhase = 'money';
                await adminSleep(ms.impactMoneyReveal);
            }
            if (impact?.animation) {
                this._triggerShiftLiveImpactPulse(impact.animation);
            }
            await adminSleep(ms.pulseAfterImpact);
            this.shiftImpactRevealPhase = 'idle';
        },

        shiftSceneAttentionClass() {
            const t = String(this.shiftAttentionTarget || '');
            if (t === 'impact') return 'ds-shift-scene--impact';
            if (t === 'focus') return 'ds-shift-scene--focus';
            if (t === 'card') return 'ds-shift-scene--exit';
            return '';
        },

        shiftFocusCardChoreoClass() {
            if (this.shiftChoreoPhase === 'exiting') return 'ds-focus-choreo-exit';
            if (this.shiftChoreoPhase === 'impact') return 'ds-focus-choreo-hidden';
            if (this.shiftChoreoPhase === 'entering') return 'ds-focus-choreo-enter';
            if (!this.shiftFocusCardVisible) return 'ds-focus-choreo-hidden';
            return '';
        },

        shiftFocusCardShown() {
            if (!this.focusCardView()) return false;
            if (this.shiftChoreoPhase === 'impact') return false;
            return this.shiftFocusCardVisible;
        },

        _abortShiftChoreo() {
            this.shiftChoreoPhase = 'idle';
            this.shiftAttentionTarget = '';
            this.shiftChoreoImpact = null;
            this.shiftFocusCardVisible = true;
            this.shiftImpactRevealPhase = 'idle';
        },

        _triggerShiftLiveImpactPulse(animation) {
            this.shiftLiveImpactPulse = String(animation || 'pulse_green');
            if (this._shiftLiveImpactTimer) clearTimeout(this._shiftLiveImpactTimer);
            this._shiftLiveImpactTimer = setTimeout(() => {
                this.shiftLiveImpactPulse = '';
                this._shiftLiveImpactTimer = null;
            }, 4000);
        },

        _triggerOwnerImpactPulse() {
            this.ownerImpactPulse = true;
            setTimeout(() => { this.ownerImpactPulse = false; }, 4000);
        },

        runShiftCompressedAction(action) {
            if (!action || this.shiftActionLoading) return;
            const act = { ...action };
            if (act.type === 'shift_action' || act.subtype) {
                const subtype = act.subtype || act.id || 'complete';
                void this.runShiftStateAction(subtype, this.focusCardView()?.id);
                return;
            }
            if (act.type === 'api' || act.type === 'navigate') {
                void this.runShiftFocusAction(act);
                return;
            }
            this.runMoneyQueueAction(act);
        },
    };
}

/** Список чатов, сообщения, takeover */
function adminMixinLiveChat() {
    return {
        /** Телефон из #chats?phone=… до первого selectChat после loadChatList */
        _pendingHashChatPhone: null,
        _adminHashListenerInstalled: false,

        _ensureAdminHashListener() {
            if (this._adminHashListenerInstalled) return;
            this._adminHashListenerInstalled = true;
            window.addEventListener('hashchange', () => {
                void this._onAdminHashChange();
            });
        },

        /** Допустимые под-вкладки «Настройки». */
        _adminSettingsTabIds: new Set(['restaurant', 'branding', 'connections', 'smart_sales', 'team', 'health', 'technical', 'bot_test']),

        _pushAdminHash() {
            if (!this.authenticated || this._applyingHashFromBrowser) return;
            const p = window.location.pathname || '/admin';
            const path = (p === '/' || p === '/admin') ? p : '/admin';
            let frag = 'dashboard';
            if (this.currentTab === 'settings') {
                const st = (this.settingsTab || 'restaurant').trim();
                frag = `settings/${encodeURIComponent(st)}`;
            } else if (this.currentTab === 'chats') {
                const ph = (this.activeChatPhone || '').trim();
                frag = ph ? `chats?phone=${encodeURIComponent(ph)}` : 'chats';
            } else if (this.currentTab === 'menu') {
                frag = this.menuView === 'stoplist' ? 'menu?view=stoplist' : 'menu';
            } else if (this.currentTab === 'inbox') {
                frag = this.inboxTab === 'system' ? 'inbox?tab=system' : 'inbox';
            } else if (this.currentTab === 'dashboard') {
                frag = this.dashboardTab === 'analytics' ? 'dashboard?tab=analytics' : 'dashboard';
            } else if (this.currentTab === 'ai_center') {
                const ac = this.aiCenterTab || 'value';
                if (ac === 'insights') frag = 'ai_center?tab=insights';
                else if (ac === 'load') frag = 'ai_center?tab=load';
                else frag = 'ai_center';
            } else if (ADMIN_TOP_TAB_IDS.has(this.currentTab)) {
                frag = this.currentTab;
            }
            const url = `${path}#${frag}`;
            try {
                window.history.replaceState(null, '', url);
            } catch (_e) {
                try {
                    window.location.hash = frag;
                } catch (_e2) { /* ignore */ }
            }
        },

        _schedulePushAdminHash() {
            if (!this.authenticated || this._applyingHashFromBrowser) return;
            if (this._hashPushTimer) clearTimeout(this._hashPushTimer);
            this._hashPushTimer = setTimeout(() => {
                this._hashPushTimer = null;
                this._pushAdminHash();
            }, 0);
        },

        _installAdminHashWatch() {
            if (this._adminHashWatchInstalled) return;
            this._adminHashWatchInstalled = true;
            try {
                this.$watch('currentTab', (tab) => {
                    this._syncModeFromTab(tab);
                    this._schedulePushAdminHash();
                });
                this.$watch('settingsTab', () => {
                    if (this.currentTab === 'settings') this._schedulePushAdminHash();
                });
                this.$watch('activeChatPhone', () => {
                    if (this.currentTab === 'chats') this._schedulePushAdminHash();
                });
                this.$watch('menuView', () => {
                    if (this.currentTab === 'menu') this._schedulePushAdminHash();
                });
                this.$watch('inboxTab', () => {
                    if (this.currentTab === 'inbox') this._schedulePushAdminHash();
                });
                this.$watch('dashboardTab', () => {
                    if (this.currentTab === 'dashboard') this._schedulePushAdminHash();
                });
                this.$watch('aiCenterTab', () => {
                    if (this.currentTab === 'ai_center') this._schedulePushAdminHash();
                });
            } catch (_e) { /* ignore */ }
        },

        /** Невыполненные шаги готовности (подсказки в шапке). */
        setupIncompleteSteps() {
            return (this.setupStatus.steps || []).filter((s) => s && !s.done);
        },

        setupDoneCount() {
            return (this.setupStatus.steps || []).filter((s) => s && s.done).length;
        },

        setupTotalCount() {
            return (this.setupStatus.steps || []).length || 0;
        },

        openSetupChecklist() {
            this.setupChecklistOpen = true;
        },

        closeSetupChecklist() {
            this.setupChecklistOpen = false;
        },

        /** Перейти к первому невыполненному шагу онбординга (вкладка настроек из API). */
        openFirstIncompleteSetupStep() {
            const next = this.setupIncompleteSteps()[0];
            const tab = next && typeof next.open_tab === 'string' ? next.open_tab.trim() : '';
            if (tab && this._adminSettingsTabIds.has(tab)) {
                this.navigateToTab('settings', { settingsTab: tab });
                return;
            }
            this.navigateToTab('settings', { settingsTab: 'connections' });
        },

        /**
         * Навигация по верхнему меню (и из кода): синхронизирует hash и грузит данные.
         * @param {string} tabId
         * @param {{ settingsTab?: string }} [opts]
         */
        _touchLazyTabMount() {
            const t = this.currentTab;
            if (t === 'chats') this.lazyTabMount.chats = true;
            else if (t === 'orders') this.lazyTabMount.orders = true;
            else if (t === 'bookings') this.lazyTabMount.bookings = true;
            else if (t === 'settings') this.lazyTabMount.settings = true;
        },

        /**
         * E5: health очереди (`GET /api/admin/system/task-queue-health`).
         * Безопасный no-op при ошибке, чтобы диагностика не ломала основной UI.
         */
        async refreshTaskQueueHealth() {
            try {
                const res = await this.apiFetch('/api/admin/system/task-queue-health');
                this.taskQueueHealthChecked = true;
                if (!res.ok) {
                    this.taskQueueHealth = null;
                    return;
                }
                const data = await res.json();
                this.taskQueueHealth = data && typeof data === 'object' ? data : null;
            } catch (_e) {
                this.taskQueueHealthChecked = true;
                this.taskQueueHealth = null;
            }
        },

        /** Класс бейджа для поля статуса очереди (redis/arq/worker). */
        taskQueueStatusClass(raw) {
            const s = String(raw || '').toLowerCase();
            if (s === 'ok') return 'ds-badge-success';
            if (s === 'degraded') return 'ds-badge-warning-soft';
            if (s === 'down') return 'ds-badge-danger';
            return 'ds-badge-neutral';
        },

        navigateToTab(tabId, opts) {
            const o = opts && typeof opts === 'object' ? opts : {};
            let tab = String(tabId || '').trim();
            if (!this.isTabVisibleForRole(tab)) {
                tab = this.resolveDefaultTabForRole();
            }
            this.currentTab = tab;
            this._syncModeFromTab(tab);
            this.ordersMobileFiltersOpen = false;
            if (tab !== 'chats') {
                this.chatMobileInfoOpen = false;
            }
            if (tab === 'settings' && typeof o.settingsTab === 'string' && o.settingsTab.trim()) {
                const st = o.settingsTab.trim();
                if (this._adminSettingsTabIds.has(st)) this.settingsTab = st;
            }
            if (tab === 'menu' && (o.menuView === 'stoplist' || o.menuView === 'catalog')) {
                this.menuView = o.menuView;
            }
            if (tab === 'inbox') {
                if (typeof o.inboxTab === 'string' && o.inboxTab.trim()) {
                    this.inboxTab = o.inboxTab.trim() === 'system' ? 'system' : 'clients';
                } else {
                    this.inboxTab = 'clients';
                }
            }
            if (tab === 'dashboard') {
                if (typeof o.dashboardTab === 'string' && o.dashboardTab.trim()) {
                    this.dashboardTab = o.dashboardTab.trim() === 'analytics' ? 'analytics' : 'overview';
                    this.analyticsDensity = this.dashboardTab === 'analytics' ? 'advanced' : 'normal';
                } else {
                    this.dashboardTab = this.analyticsDensity === 'advanced' ? 'analytics' : 'overview';
                }
                this._persistAnalyticsDensity();
            }
            if (tab === 'ai_center') {
                if (typeof o.aiCenterTab === 'string' && o.aiCenterTab.trim()) {
                    const a = o.aiCenterTab.trim();
                    if (a === 'insights') this.aiCenterTab = 'insights';
                    else if (a === 'load') this.aiCenterTab = 'load';
                    else if (a === 'os') this.aiCenterTab = 'os';
                    else if (a === 'guestcare') this.aiCenterTab = 'guestcare';
                    else if (a === 'final_mile') this.aiCenterTab = 'final_mile';
                    else this.aiCenterTab = 'value';
                } else {
                    this.aiCenterTab = 'value';
                }
            }
            if (tab !== 'inbox') this.inboxTab = 'clients';
            if (tab !== 'dashboard') this.dashboardTab = 'overview';
            if (tab !== 'ai_center') this.aiCenterTab = 'value';
            if (tab === 'chats') {
                if (typeof o.chatPulseFilter === 'string' && o.chatPulseFilter.trim()) {
                    this.chatPulseFilter = o.chatPulseFilter.trim();
                } else {
                    this.chatPulseFilter = '';
                }
            } else {
                this.chatPulseFilter = '';
            }
            if (tab === 'orders') {
                if (o.orderSumMin != null && o.orderSumMin !== '') {
                    this.orderSumMin = Number(o.orderSumMin);
                    this.ordersView = 'table';
                    this.ordersPage = 1;
                }
            }
            this.sidebarOpen = false;
            void this.loadTabData();
            this._schedulePushAdminHash();
        },

        // p15:tour — coach-marks по ключевым разделам (localStorage; `?first_run=1` принудительно).
        p15TourStorageKey() {
            const em = (this.userData && this.userData.email) ? String(this.userData.email).toLowerCase() : 'anon';
            return `rm_p15_admin_tour_v1::${em}`;
        },

        p15TourSteps() {
            return [
                { id: 'inbox', tab: 'inbox', settingsTab: null, selector: '[data-p15-tour="inbox"]', title: 'Входящие', text: 'Очередь от клиентов и системные инциденты — проверяйте в начале смены.' },
                { id: 'orders', tab: 'orders', settingsTab: null, selector: '[data-p15-tour="orders"]', title: 'Заказы', text: 'Канбан и карточки: статусы, кухня, оплата и доставка в WhatsApp.' },
                { id: 'bot', tab: 'settings', settingsTab: 'bot_test', selector: '[data-p15-tour="settings-bot"]', title: 'Бот / ИИ', text: 'Песочница для проверки ответов и сценариев без влияния на гостей.' },
                { id: 'brand', tab: 'settings', settingsTab: 'branding', selector: '[data-p15-tour="settings-brand"]', title: 'Бренд', text: 'Название и цвет сети — визуальный якорь в шапке и сайдбаре.' },
                { id: 'knowledge', tab: 'settings', settingsTab: 'restaurant', selector: '[data-p15-tour="settings-knowledge"]', title: 'База знаний', text: 'Факты о заведении для бота: режим, парковка, банкеты и т.д.' },
            ];
        },

        maybeStartP15CoachTour() {
            if (!this.authenticated || this.isDemoSession) return;
            let force = false;
            try {
                const p = new URLSearchParams(window.location.search || '');
                force = p.get('first_run') === '1';
            } catch (_e) { /* ignore */ }
            let done = false;
            try {
                done = window.localStorage.getItem(this.p15TourStorageKey()) === '1';
            } catch (_e) { /* ignore */ }
            if (!force && !done && this.userData && this.userData.tour_completed_at) {
                done = true;
                try { window.localStorage.setItem(this.p15TourStorageKey(), '1'); } catch (_e2) { /* ignore */ }
            }
            if (!force && done) return;
            if (this._p15TourOnResize) {
                try { window.removeEventListener('resize', this._p15TourOnResize); } catch (_e2) { /* ignore */ }
            }
            this._p15TourOnResize = () => {
                if (!this.p15TourActive) return;
                this.refreshP15TourAnchor();
            };
            try {
                window.addEventListener('resize', this._p15TourOnResize, { passive: true });
            } catch (_e) { /* ignore */ }
            this.p15TourStepIndex = 0;
            this.p15TourActive = true;
            this.$nextTick(() => this.refreshP15TourAnchor());
        },

        refreshP15TourAnchor() {
            if (!this.p15TourActive) return;
            const steps = this.p15TourSteps();
            const step = steps[this.p15TourStepIndex];
            if (!step) return;
            if (step.tab === 'settings' && step.settingsTab) {
                this.navigateToTab('settings', { settingsTab: step.settingsTab });
            } else if (this.currentTab !== step.tab) {
                this.navigateToTab(step.tab);
            } else if (step.settingsTab && this.settingsTab !== step.settingsTab) {
                this.setSettingsTab(step.settingsTab);
            }
            const layout = () => {
                const el = document.querySelector(step.selector);
                if (el && typeof el.getBoundingClientRect === 'function') {
                    const r = el.getBoundingClientRect();
                    const pad = 8;
                    this.p15TourRect = {
                        top: r.top - pad,
                        left: r.left - pad,
                        width: r.width + pad * 2,
                        height: r.height + pad * 2,
                    };
                    let top = r.bottom + 12;
                    let left = r.left;
                    if (top + 220 > window.innerHeight) {
                        top = Math.max(12, r.top - 200);
                    }
                    left = Math.min(window.innerWidth - 320, Math.max(12, left));
                    this.p15TourPopoverStyle = `top:${Math.round(top)}px;left:${Math.round(left)}px;`;
                } else {
                    this.p15TourRect = { top: 80, left: 80, width: 120, height: 48 };
                    this.p15TourPopoverStyle = 'top:120px;left:16px;';
                }
            };
            this.$nextTick(() => {
                requestAnimationFrame(() => setTimeout(layout, 60));
            });
        },

        p15TourNext() {
            const steps = this.p15TourSteps();
            if (this.p15TourStepIndex + 1 >= steps.length) {
                this.finishP15CoachTour();
                return;
            }
            this.p15TourStepIndex += 1;
            const next = steps[this.p15TourStepIndex];
            if (next && next.id === 'knowledge') {
                this.$nextTick(() => {
                    try {
                        document.getElementById('settings-restaurant-knowledge')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    } catch (_e) { /* ignore */ }
                    setTimeout(() => this.refreshP15TourAnchor(), 450);
                });
                return;
            }
            this.refreshP15TourAnchor();
        },

        p15TourSkip() {
            this.finishP15CoachTour();
        },

        finishP15CoachTour() {
            this.p15TourActive = false;
            this.p15TourStepIndex = 0;
            const completedAt = new Date().toISOString();
            try {
                window.localStorage.setItem(this.p15TourStorageKey(), '1');
            } catch (_e) { /* ignore */ }
            void this.apiJsonResponse('/api/admin/auth/tour-complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ completed_at: completedAt }),
            }).then(({ ok, data }) => {
                if (ok && data?.tour_completed_at && this.userData) {
                    this.userData.tour_completed_at = data.tour_completed_at;
                }
            }).catch(() => { /* noop */ });
            if (this._p15TourOnResize) {
                try { window.removeEventListener('resize', this._p15TourOnResize); } catch (_e2) { /* ignore */ }
                this._p15TourOnResize = null;
            }
            try {
                const u = new URL(window.location.href);
                if (u.searchParams.get('first_run') === '1') {
                    u.searchParams.delete('first_run');
                    window.history.replaceState({}, '', `${u.pathname}${u.search}${u.hash}`);
                }
            } catch (_e) { /* ignore */ }
        },

        setMenuView(view) {
            const v = view === 'stoplist' ? 'stoplist' : 'catalog';
            this.menuView = v;
            if (this.currentTab === 'menu') {
                void this.loadTabData();
                this._schedulePushAdminHash();
            }
        },

        _applyParsedHash(parsed) {
            this._pendingHashChatPhone = null;
            const tab = parsed?.tab;
            const phone = parsed?.phone || null;
            const settingsTab = parsed?.settingsTab || null;
            if (!tab) return;
            if (tab === 'settings') {
                this.currentTab = 'settings';
                if (settingsTab && this._adminSettingsTabIds.has(settingsTab)) {
                    this.settingsTab = settingsTab;
                }
                return;
            }
            if (!ADMIN_TOP_TAB_IDS.has(tab)) {
                this.currentTab = 'dashboard';
                this.dashboardTab = 'overview';
                return;
            }
            this.currentTab = tab;
            if (tab === 'menu') {
                const mv = parsed?.menuView;
                this.menuView = mv === 'stoplist' || mv === 'catalog' ? mv : 'catalog';
            } else if (tab !== 'menu') {
                this.menuView = 'catalog';
            }
            if (tab === 'inbox') {
                this.inboxTab = parsed?.inboxTab === 'system' ? 'system' : 'clients';
            } else {
                this.inboxTab = 'clients';
            }
            if (tab === 'dashboard') {
                this.dashboardTab = parsed?.dashboardTab === 'analytics' ? 'analytics' : 'overview';
            } else {
                this.dashboardTab = 'overview';
            }
            if (tab === 'ai_center') {
                const ac = parsed?.aiCenterTab;
                if (ac === 'insights') this.aiCenterTab = 'insights';
                else if (ac === 'load') this.aiCenterTab = 'load';
                else if (ac === 'os') this.aiCenterTab = 'os';
                else if (ac === 'guestcare') this.aiCenterTab = 'guestcare';
                else if (ac === 'final_mile') this.aiCenterTab = 'final_mile';
                else this.aiCenterTab = 'value';
            } else {
                this.aiCenterTab = 'value';
            }
            if (tab === 'chats' && phone) this._pendingHashChatPhone = phone;
        },

        /** Перед loadTabData: открыть вкладку по hash (глубокие ссылки). */
        _applyAdminHashBeforeFirstPaint() {
            this._applyParsedHash(adminParseLocationHash());
        },

        /** Совместимость: URL чата — общий _pushAdminHash. */
        syncAdminChatsHash(_phoneIgnored) {
            this._pushAdminHash();
        },

        async _consumePendingHashChatPhone() {
            const p = this._pendingHashChatPhone;
            this._pendingHashChatPhone = null;
            if (!p) return;
            await this.selectChat(p);
        },

        async _onAdminHashChange() {
            if (!this.authenticated) return;
            this._applyingHashFromBrowser = true;
            try {
                const parsed = adminParseLocationHash();
                this._applyParsedHash(parsed);
                await this.loadTabData();
                if (this.currentTab === 'chats') {
                    await this.loadChatList();
                    if (parsed.phone) await this.selectChat(parsed.phone);
                    else {
                        this.activeChatPhone = '';
                        this.chatMobileInfoOpen = false;
                    }
                }
            } finally {
                this._applyingHashFromBrowser = false;
            }
        },

        // ─── Live Chat ───────────────────────────────
        async loadChatList(reset = true) {
            if (this.chatListLoading) return;
            if (!reset && !this.chatListHasMore) return;
            this.chatListLoading = true;
            try {
                const limit = 60;
                let url = `/api/admin/chats?limit=${limit}&mode=${encodeURIComponent(this.chatTriageMode || 'active')}`;
                const locationParams = this.locationQueryParams();
                const locationQuery = locationParams.toString();
                if (locationQuery) url += `&${locationQuery}`;
                if (!reset) {
                    if (this.chatListCursorAt) url += `&cursor_at=${encodeURIComponent(String(this.chatListCursorAt))}`;
                    if (this.chatListCursorId) url += `&cursor_id=${encodeURIComponent(String(this.chatListCursorId))}`;
                }
                const res = await this.apiFetch(url);
                if (!res.ok) {
                    adminLogger.warn('GET /api/admin/chats', res.status);
                    return;
                }
                const data = await res.json();
                const incoming = data.chats || [];
                this.botSlaStatus = {
                    bot_short_mode: !!data.bot_short_mode,
                    slow_chats: Number(data.slow_chats || 0),
                    location_id: data.location_id ?? null,
                };
                const preserved = new Map(
                    this.chatList.filter((c) => c.unread).map((c) => [c.phone, c]),
                );

                const mapped = incoming.map((c) => {
                    const prev = preserved.get(c.phone);
                    return {
                        phone: c.phone,
                        lastMessage: c.lastMessage || '',
                        lastAt: c.lastAt || null,
                        state: c.state || 'chatting',
                        unread: prev ? prev.unread : !!c.unread,
                        userName: c.userName ?? prev?.userName,
                        triage: c.triage || {},
                        triageState: c.triageState || c.triage?.state || 'active',
                        assignee: c.assignee || c.triage?.assignee || '',
                        snoozedUntil: c.snoozedUntil || c.triage?.snoozed_until || null,
                        botShortMode: !!c.bot_short_mode,
                        slowChats: Number(c.slow_chats || 0),
                        lastRole: c.last_role || 'assistant',
                        waitSeconds: c.wait_seconds ?? null,
                        pulse: c.pulse || c.sla_status || 'green',
                        slaStatus: c.pulse || c.sla_status || 'green',
                        chatSlow: !!c.chat_slow,
                        locationId: c.location_id ?? null,
                    };
                });

                if (reset) {
                    this.chatList = mapped;
                } else {
                    const seen = new Set(this.chatList.map((c) => c.phone));
                    for (const c of mapped) {
                        if (!c?.phone || seen.has(c.phone)) continue;
                        this.chatList.push(c);
                        seen.add(c.phone);
                    }
                }

                this.chatListHasMore = !!data.has_more;
                this.chatListCursorAt = data.next_cursor?.cursor_at ?? null;
                this.chatListCursorId = data.next_cursor?.cursor_id ?? null;
                this.unreadChats = this.chatList.filter((c) => c.unread).length;

                // Prefetch топ-3 чата (не текущий) — через 600мс чтобы не конкурировать с основными запросами
                if (reset) this._scheduleChatPrefetch();
            } catch (e) {
                adminLogger.warn('loadChatList', e);
            } finally {
                this.chatListLoading = false;
            }
        },

        _scheduleChatPrefetch() {
            clearTimeout(this._chatPrefetchTimer);
            this._chatPrefetchTimer = setTimeout(() => {
                const toFetch = this.chatList
                    .filter(c => c.phone && c.phone !== this.activeChatPhone && !this._chatCacheGet(c.phone))
                    .slice(0, 3);
                for (const chat of toFetch) {
                    this.apiFetch(`/api/admin/chats/${encodeURIComponent(chat.phone)}?limit=50`)
                        .then(r => r.ok ? r.json() : null)
                        .then(d => {
                            if (d) this._chatCacheSet(chat.phone, d.messages || [], !!d.has_more, d.next_before_id ?? null);
                        })
                        .catch(() => {});
                }
            }, 600);
        },

        async loadMoreChats() {
            await this.loadChatList(false);
        },

        async setChatTriageMode(mode) {
            this.chatTriageMode = mode || 'active';
            this.chatList = [];
            this.chatListHasMore = true;
            this.chatListCursorAt = null;
            this.chatListCursorId = null;
            await this.loadChatList(true);
        },

        _mergeChatTriage(phone, triage) {
            const idx = this.chatList.findIndex((c) => c.phone === phone);
            if (idx < 0) return;
            this.chatList[idx].triage = triage || {};
            this.chatList[idx].triageState = triage?.state || 'active';
            this.chatList[idx].assignee = triage?.assignee || '';
            this.chatList[idx].snoozedUntil = triage?.snoozed_until || null;
        },

        async postChatTriageAction(action, payload = {}) {
            const phone = this.activeChatPhone;
            if (!phone) return;
            try {
                const res = await this.apiFetch(`/api/admin/chats/${encodeURIComponent(phone)}/${action}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!res.ok) throw new Error(`chat triage ${res.status}`);
                const data = await res.json();
                this._mergeChatTriage(phone, data.triage);
                if (action === 'close' && this.chatTriageMode !== 'closed') {
                    this.chatList = this.chatList.filter((c) => c.phone !== phone);
                    this.activeChatPhone = '';
                }
            } catch (e) {
                adminLogger.warn('postChatTriageAction', e);
                this.flashToast('Не удалось обновить диалог', 'error', 3500);
            }
        },

        async snoozeActiveChat(minutes) {
            await this.postChatTriageAction('snooze', { minutes: Number(minutes) || 30 });
            if (this.chatTriageMode === 'active') {
                this.chatList = this.chatList.filter((c) => c.phone !== this.activeChatPhone);
                this.activeChatPhone = '';
            }
        },

        onChatListScroll(e) {
            const el = e?.target;
            if (!el) return;
            if (this.chatListLoading || !this.chatListHasMore) return;
            // Near bottom.
            const remaining = (el.scrollHeight - el.scrollTop - el.clientHeight);
            if (remaining < 260) {
                void this.loadMoreChats();
            }
        },

        _chatCacheGet(phone) {
            if (!this._chatMsgCache) return null;
            const entry = this._chatMsgCache.get(phone);
            if (!entry) return null;
            // TTL 5 минут — после этого данные устарели
            if (Date.now() - entry.ts > 300_000) { this._chatMsgCache.delete(phone); return null; }
            return entry;
        },
        _chatCacheSet(phone, messages, hasMore, beforeId) {
            if (!this._chatMsgCache) this._chatMsgCache = new Map();
            // Выбрасываем самый старый если >15 чатов
            if (this._chatMsgCache.size >= 15 && !this._chatMsgCache.has(phone)) {
                this._chatMsgCache.delete(this._chatMsgCache.keys().next().value);
            }
            this._chatMsgCache.set(phone, { messages: [...messages], hasMore, beforeId, ts: Date.now() });
        },
        _chatCacheInvalidate(phone) {
            this._chatMsgCache?.delete(phone);
        },

        async selectChat(phone) {
            if (!phone?.trim()) return;
            phone = phone.trim();
            const requestId = Symbol(phone);
            this._selectChatRequestId = requestId;
            this.chatMobileInfoOpen = false;
            this.activeChatPhone = phone;
            this.chatMessagesHasMore = false;
            this.chatMessagesBeforeId = null;
            this.chatMessagesLoadingOlder = false;
            this.customerSummaryLoading = true;
            this.customerSummary = {
                user_exists: false, phone, name: null,
                total_orders: 0, revenue_orders: 0, total_spent: 0, avg_check: 0,
                is_blocked: false, ai_paused: false, ai_snoozed_until: null,
                operator_note: '', last_escalation: null,
            };
            this.guestContext = { activeOrder: null, activeBooking: null };
            this.guestContextLoading = true;
            this.activeChatTraceId = '';

            const chatIdx = this.chatList.findIndex(c => c.phone === phone);
            if (chatIdx >= 0) {
                this.chatList[chatIdx].unread = false;
                this.unreadChats = this.chatList.filter(c => c.unread).length;
            } else {
                this.chatList.unshift({ phone, lastMessage: '', state: 'chatting', unread: false });
            }

            // Показываем кэш мгновенно — пока грузятся свежие данные
            const cached = this._chatCacheGet(phone);
            if (cached) {
                this.chatMessages = cached.messages;
                this.chatMessagesHasMore = cached.hasMore;
                this.chatMessagesBeforeId = cached.beforeId;
                this.scrollChatToBottom();
            }

            const enc = encodeURIComponent(phone);
            const qo = new URLSearchParams({ q: phone, size: '40', page: '1' });
            const qb = new URLSearchParams({ q: phone, limit: '40' });
            const locParams = this.locationQueryParams();
            const locQuery = locParams.toString();
            if (locQuery) {
                for (const [k, v] of locParams.entries()) {
                    qo.set(k, v);
                    qb.set(k, v);
                }
            }
            const stateUrl = `/api/admin/chats/${enc}/state${locQuery ? `?${locQuery}` : ''}`;
            const messagesUrl = `/api/admin/chats/${enc}?limit=50${locQuery ? `&${locQuery}` : ''}`;

            // Все 5 запросов параллельно — ни один не зависит от другого
            const [stateRes, msgsRes, summaryRes, ordersRes, bookingsRes] = await Promise.all([
                this.apiFetch(stateUrl).catch(() => null),
                this.apiFetch(messagesUrl).catch(() => null),
                this.apiFetch(`/api/admin/customers/${enc}/summary`).catch(() => null),
                this.apiFetch(`/api/admin/orders?${qo}`).catch(() => null),
                this.apiFetch(`/api/admin/bookings?${qb}`).catch(() => null),
            ]);

            if (this._selectChatRequestId !== requestId || this.activeChatPhone !== phone) return;

            // state
            if (stateRes?.ok) {
                const d = await stateRes.json();
                this.activeChatState = d.state;
                this.activeChatTraceId = String(d.latest_trace_id || '').trim();
                this.botSlaStatus = {
                    bot_short_mode: !!d.bot_short_mode,
                    slow_chats: Number(d.slow_chats || 0),
                    location_id: d.location_id ?? null,
                };
                if (d.ai_snoozed_until != null && this.customerSummary) {
                    this.customerSummary.ai_snoozed_until = d.ai_snoozed_until;
                }
                const uix = this.chatList.findIndex(c => c.phone === phone);
                if (uix >= 0) {
                    this.chatList[uix].state = d.state;
                    this.chatList[uix].botShortMode = !!d.bot_short_mode;
                    this.chatList[uix].slowChats = Number(d.slow_chats || 0);
                    this.chatList[uix].slaStatus = d.sla_status || 'green';
                    this.chatList[uix].chatSlow = !!d.chat_slow;
                }
            }

            // messages
            if (msgsRes?.ok) {
                const d = await msgsRes.json();
                this.chatMessages = d.messages || [];
                this.chatMessagesHasMore = !!d.has_more;
                this.chatMessagesBeforeId = d.next_before_id ?? null;
                const uix = this.chatList.findIndex(c => c.phone === phone);
                if (uix >= 0 && d.user_name) this.chatList[uix].userName = d.user_name;
                this._chatCacheSet(phone, this.chatMessages, this.chatMessagesHasMore, this.chatMessagesBeforeId);
                if (!cached) this.scrollChatToBottom();
            } else if (!cached) {
                this.chatMessages = [];
            }

            // customerSummary
            if (summaryRes?.ok) {
                const d = await summaryRes.json();
                this.customerSummary = {
                    user_exists: !!d.user_exists, phone: d.phone || phone, name: d.name ?? null,
                    total_orders: d.total_orders ?? 0, revenue_orders: d.revenue_orders ?? 0,
                    total_spent: d.total_spent ?? 0, avg_check: d.avg_check ?? 0,
                    is_blocked: !!d.is_blocked, ai_paused: !!d.ai_paused,
                    ai_snoozed_until: d.ai_snoozed_until ?? null,
                    operator_note: d.operator_note ?? '',
                    last_escalation: d.last_escalation && typeof d.last_escalation === 'object'
                        ? d.last_escalation : null,
                };
            }
            this.customerSummaryLoading = false;

            // orders + bookings
            let orders = [], bookings = [];
            if (ordersRes?.ok) { const d = await ordersRes.json(); orders = Array.isArray(d.items) ? d.items : (d.orders || []); }
            if (bookingsRes?.ok) { const d = await bookingsRes.json(); bookings = d.bookings || []; }
            this.guestContext = {
                activeOrder: this._pickActiveGuestOrder(orders),
                activeBooking: this._pickActiveGuestBooking(bookings),
            };
            this.guestContextLoading = false;

            this.syncAdminChatsHash(phone);
        },

        async loadOlderChatMessages() {
            if (!this.activeChatPhone) return;
            if (!this.chatMessagesHasMore) return;
            if (this.chatMessagesLoadingOlder) return;
            const beforeId = this.chatMessagesBeforeId;
            if (!beforeId) return;

            const area = document.getElementById('admin-chat-messages');
            const prevScrollHeight = area ? area.scrollHeight : 0;
            const prevScrollTop = area ? area.scrollTop : 0;

            this.chatMessagesLoadingOlder = true;
            try {
                const phone = this.activeChatPhone;
                const locQuery = this.locationQueryParams().toString();
                const res = await this.apiFetch(`/api/admin/chats/${encodeURIComponent(phone)}?limit=50&before_id=${encodeURIComponent(String(beforeId))}${locQuery ? `&${locQuery}` : ''}`);
                if (!res.ok) return;
                const data = await res.json();
                const older = data.messages || [];
                if (!older.length) {
                    this.chatMessagesHasMore = false;
                    this.chatMessagesBeforeId = null;
                    return;
                }
                this.chatMessages = [...older, ...this.chatMessages];
                this.chatMessagesHasMore = !!data.has_more;
                this.chatMessagesBeforeId = data.next_before_id ?? null;
            } catch (_e) {
                // ignore
            } finally {
                this.chatMessagesLoadingOlder = false;
                this.$nextTick(() => {
                    const a = document.getElementById('admin-chat-messages');
                    if (!a) return;
                    const newScrollHeight = a.scrollHeight;
                    const delta = newScrollHeight - prevScrollHeight;
                    a.scrollTop = Math.max(0, prevScrollTop + delta);
                });
            }
        },

        onChatMessagesScroll(e) {
            const el = e?.target;
            if (!el) return;
            if (this.chatMessagesLoadingOlder || !this.chatMessagesHasMore) return;
            if (el.scrollTop < 80) {
                void this.loadOlderChatMessages();
            }
        },

        /** Мобилка: вернуться к списку диалогов */
        backFromMobileChat() {
            this.chatMobileInfoOpen = false;
            this.activeChatPhone = '';
            this.syncAdminChatsHash('');
        },

        async loadCustomerSummary(phone) {
            if (!phone?.trim()) {
                this.customerSummaryLoading = false;
                this.customerSummaryError = null;
                this.guestContextLoading = false;
                return;
            }
            const key = phone.trim();
            const enc = encodeURIComponent(key);
            this.customerSummaryLoading = true;
            this.customerSummaryError = null;
            const timeoutMs = 12000;
            let timedOut = false;
            const timer = setTimeout(() => { timedOut = true; }, timeoutMs);
            try {
                const res = await this.apiFetch(`/api/admin/customers/${enc}/summary`);
                if (this.activeChatPhone?.trim() !== key) return;
                if (timedOut) {
                    this.customerSummaryError = 'timeout';
                    return;
                }
                if (res.ok) {
                    const data = await res.json();
                    this.customerSummary = {
                        user_exists: !!data.user_exists,
                        phone: data.phone || key,
                        name: data.name ?? null,
                        total_orders: data.total_orders ?? 0,
                        revenue_orders: data.revenue_orders ?? 0,
                        total_spent: data.total_spent ?? 0,
                        avg_check: data.avg_check ?? 0,
                        is_blocked: !!data.is_blocked,
                        ai_paused: !!data.ai_paused,
                        ai_snoozed_until: data.ai_snoozed_until ?? null,
                        operator_note: data.operator_note ?? '',
                        last_escalation: data.last_escalation && typeof data.last_escalation === 'object'
                            ? data.last_escalation
                            : null,
                    };
                    this.customerSummaryError = null;
                } else {
                    this.customerSummaryError = res.status === 404 ? 'empty' : 'error';
                }
            } catch (e) {
                if (this.activeChatPhone?.trim() !== key) return;
                adminLogger.error('loadCustomerSummary', e);
                this.customerSummaryError = timedOut ? 'timeout' : 'error';
            } finally {
                clearTimeout(timer);
                if (this.activeChatPhone?.trim() === key) {
                    this.customerSummaryLoading = false;
                }
            }
            if (this.activeChatPhone?.trim() === key && !this.customerSummaryError) {
                await this.loadGuestContextOrdersBookings(key);
            } else if (this.activeChatPhone?.trim() === key) {
                this.guestContextLoading = false;
            }
        },

        // p15:context
        async loadGuestContextOrdersBookings(phone) {
            const key = (phone || '').trim();
            if (!key) {
                this.guestContextLoading = false;
                this.guestContext = { activeOrder: null, activeBooking: null };
                return;
            }
            if (this.activeChatPhone?.trim() !== key) return;
            this.guestContextLoading = true;
            try {
                const qo = new URLSearchParams({ q: key, size: '40', page: '1' });
                const qb = new URLSearchParams({ q: key, limit: '40' });
                for (const [k, v] of this.locationQueryParams().entries()) {
                    qo.set(k, v);
                    qb.set(k, v);
                }
                const [ro, rb] = await Promise.all([
                    this.apiFetch(`/api/admin/orders?${qo.toString()}`),
                    this.apiFetch(`/api/admin/bookings?${qb.toString()}`),
                ]);
                if (this.activeChatPhone?.trim() !== key) return;
                let orders = [];
                let bookings = [];
                if (ro.ok) {
                    const d = await ro.json();
                    orders = Array.isArray(d.items) ? d.items : (d.orders || []);
                }
                if (rb.ok) {
                    const d = await rb.json();
                    bookings = d.bookings || [];
                }
                this.guestContext = {
                    activeOrder: this._pickActiveGuestOrder(orders),
                    activeBooking: this._pickActiveGuestBooking(bookings),
                };
            } catch (e) {
                adminLogger.error('loadGuestContextOrdersBookings', e);
                this.guestContext = { activeOrder: null, activeBooking: null };
            } finally {
                if (this.activeChatPhone?.trim() === key) {
                    this.guestContextLoading = false;
                }
            }
        },

        _pickActiveGuestOrder(orders) {
            const st = new Set(['draft', 'confirmed', 'sending_to_iiko', 'sent_to_iiko']);
            const rows = (orders || []).filter((o) => o && st.has(String(o.status || '')));
            if (!rows.length) return null;
            rows.sort((a, b) => {
                const ta = Date.parse(a.created_at || '') || 0;
                const tb = Date.parse(b.created_at || '') || 0;
                return tb - ta;
            });
            return rows[0];
        },

        _pickActiveGuestBooking(bookings) {
            if (!bookings?.length) return null;
            const open = bookings.filter((b) => !['cancelled'].includes(String(b.status || '').toLowerCase()));
            if (!open.length) return null;
            const withDt = open.map((b) => {
                const t = Date.parse(`${b.date || ''}T${String(b.time || '').slice(0, 8)}`);
                return { b, t: Number.isFinite(t) ? t : 0 };
            }).filter((x) => x.t > 0);
            if (!withDt.length) return open[0];
            const now = Date.now();
            const future = withDt.filter((x) => x.t >= now - 3600000).sort((a, c) => a.t - c.t);
            if (future.length) return future[0].b;
            withDt.sort((a, c) => c.t - a.t);
            return withDt[0].b;
        },

        openGuestContextOrder(o) {
            if (!o?.id) return;
            const id = Number(o.id);
            this.navigateToTab('orders');
            this.$nextTick(() => {
                const found = (this.orders || []).find((x) => Number(x.id) === id);
                this.openOrderDetails(found || o);
            });
        },

        openGuestContextBooking(b) {
            if (!b?.id) return;
            this.navigateToTab('bookings');
            this.$nextTick(async () => {
                await this.loadTabData();
                const found = (this.bookings || []).find((x) => Number(x.id) === Number(b.id));
                if (!found) {
                    this.flashToast('Обновите список броней или откройте по №' + b.id, 'info', 4000);
                }
            });
        },

        async saveCustomerNote() {
            const p = this.activeChatPhone?.trim();
            if (!p || !this.customerSummary.user_exists) return;
            try {
                await this.apiFetch(`/api/admin/customers/${encodeURIComponent(p)}/note`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ note: this.customerSummary.operator_note || '' }),
                });
                this.customerNoteSavedFlash = true;
                if (this._customerNoteSavedTimer) clearTimeout(this._customerNoteSavedTimer);
                this._customerNoteSavedTimer = setTimeout(() => {
                    this.customerNoteSavedFlash = false;
                    this._customerNoteSavedTimer = null;
                }, 2000);
            } catch (e) {
                adminLogger.error('saveCustomerNote', e);
            }
        },

        applyCannedResponse(text) {
            if (!text || this.chatOperatorInputDisabled()) return;
            const cur = this.operatorInput || '';
            this.operatorInput = cur.trim() ? `${cur.trim()}\n${text}` : text;
        },

        scrollChatToBottom() {
            this.$nextTick(() => {
                const area = document.getElementById('admin-chat-messages');
                if (area) area.scrollTop = area.scrollHeight;
            });
        },

        customerAiSnoozeActive() {
            const iso = this.customerSummary?.ai_snoozed_until;
            if (!iso) return false;
            const t = Date.parse(iso);
            return Number.isFinite(t) && t > Date.now();
        },

        customerAiSnoozeUntilLabel() {
            if (!this.customerAiSnoozeActive()) return '';
            try {
                const d = new Date(this.customerSummary.ai_snoozed_until);
                return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            } catch {
                return '';
            }
        },

        async setChatAiSnoozePreset(preset) {
            const p = this.activeChatPhone?.trim();
            if (!p) return;
            try {
                const res = await this.apiFetch(`/api/admin/chats/${encodeURIComponent(p)}/ai-snooze`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ preset }),
                });
                const data = await res.json().catch(() => ({}));
                if (res.ok) {
                    if (this.customerSummary) {
                        this.customerSummary.ai_paused = !!data.ai_paused;
                        this.customerSummary.ai_snoozed_until = data.ai_snoozed_until || null;
                    }
                    if (data.ai_paused) {
                        this.activeChatState = 'human_mode';
                    } else {
                        this.activeChatState = 'chatting';
                    }
                    const ix = this.chatList.findIndex((c) => c.phone === p);
                    if (ix >= 0) this.chatList[ix].state = this.activeChatState;
                    this.flashToast(preset === 'off' ? 'Пауза ИИ снята' : 'Режим ИИ обновлён', 'success', 2200);
                } else {
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Не удалось изменить паузу ИИ', 'Ошибка');
                }
            } catch {
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            }
        },

        /** true — бот/ИИ ведёт диалог, поле ввода заблокировано */
        chatIsBotActive() {
            if (this.customerSummary?.user_exists && this.customerSummary.ai_paused) {
                return false;
            }
            if (this.customerSummary?.user_exists && this.customerAiSnoozeActive()) {
                return false;
            }
            return this.activeChatState !== 'human_mode';
        },

        async toggleTakeover() {
            if (!this.activeChatPhone) return;
            const phone = this.activeChatPhone;
            const goingHuman = this.activeChatState !== 'human_mode';
            const endpoint = goingHuman ? 'takeover' : 'release';
            const targetState = goingHuman ? 'human_mode' : 'chatting';
            try {
                const res = await this.apiFetch(`/api/admin/chats/${phone}/${endpoint}`, { method: 'POST' });
                if (!res.ok) throw new Error('takeover/release failed');
                this.activeChatState = targetState;
                const idx = this.chatList.findIndex(c => c.phone === phone);
                if (idx >= 0) this.chatList[idx].state = targetState;
            } catch (_e) {
                this.flashToast('Не удалось изменить режим диалога. Проверьте соединение.', 'error', 4500);
            }
        },

        handleOperatorEnter(e) {
            if (e.shiftKey) return;
            e.preventDefault();
            this.sendOperatorMessage();
        },

        chatInitials(chat) {
            const n = (chat && chat.userName) ? String(chat.userName).trim() : '';
            if (n.length) return n.charAt(0).toUpperCase();
            const p = (chat && chat.phone) ? String(chat.phone).replace(/\D/g, '') : '';
            if (p.length >= 2) return p.slice(-2);
            return '#';
        },

        activeChatInitials() {
            const c = this.chatList.find(x => x.phone === this.activeChatPhone);
            return this.chatInitials(c || { phone: this.activeChatPhone });
        },

        activeChatTitle() {
            const c = this.chatList.find(x => x.phone === this.activeChatPhone);
            return (c && c.userName) ? c.userName : this.activeChatPhone;
        },

        async takeoverChat() {
            if (!this.activeChatPhone) return;
            const phone = this.activeChatPhone;
            try {
                const res = await this.apiFetch(`/api/admin/chats/${phone}/takeover`, { method: 'POST' });
                if (!res.ok) throw new Error('takeover failed');
                this.activeChatState = 'human_mode';
                const idx = this.chatList.findIndex(c => c.phone === phone);
                if (idx >= 0) this.chatList[idx].state = 'human_mode';
            } catch {
                this.flashToast('Не удалось перехватить диалог. Попробуйте ещё раз.', 'error', 4500);
            }
        },

        async releaseChat() {
            if (!this.activeChatPhone) return;
            const phone = this.activeChatPhone;
            try {
                const res = await this.apiFetch(`/api/admin/chats/${phone}/release`, { method: 'POST' });
                if (!res.ok) throw new Error('release failed');
                this.activeChatState = 'chatting';
                const idx = this.chatList.findIndex(c => c.phone === phone);
                if (idx >= 0) this.chatList[idx].state = 'chatting';
            } catch {
                this.flashToast('Не удалось вернуть ИИ. Попробуйте ещё раз.', 'error', 4500);
            }
        },

        async sendOperatorMessage() {
            const text = this.operatorInput.trim();
            if (!text || !this.activeChatPhone || this.chatOperatorInputDisabled()) return;
            this.operatorInput = '';

            try {
                const res = await this.apiFetch(`/api/admin/chats/${encodeURIComponent(this.activeChatPhone)}/send_message`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text }),
                });
                if (!res.ok) throw new Error(`send_message ${res.status}`);
                this.scrollChatToBottom();
            } catch (_e) {
                this.operatorInput = text;
                void this.showUiAlert('Сообщение не отправлено. Текст возвращён в поле.', 'Ошибка');
            }
        },

    };
}

/** loadTabData, графики, настройки, меню CRUD, тест бота */
function adminMixinDataChartsSettings() {
    return {
        // ─── Data Loading ────────────────────────────
        async loadTabData() {
            // Старые клиенты/закладки могли держать currentTab === 'analytics' до P1.5.0 — без нормализации блок аналитики не показывался.
            if (this.currentTab === 'analytics') {
                this.currentTab = 'dashboard';
                this.dashboardTab = 'analytics';
            }
            // Legacy ids → Settings tabs (backward compatibility for old sidebar/hash links)
            const legacySettingsMap = {
                integrations: 'connections',
                packaging: 'restaurant',
                knowledge: 'restaurant',
                upsell: 'smart_sales',
                team: 'team',
                test: 'bot_test',
            };
            const legacyTab = legacySettingsMap[this.currentTab];
            if (legacyTab) {
                this.currentTab = 'settings';
                this.settingsTab = legacyTab;
            }
            // Legacy #errors / старый tab id → inbox (P1.5.0)
            if (this.currentTab === 'errors' || this.currentTab === 'operator_queue') {
                this.currentTab = 'inbox';
                this.inboxTab = 'clients';
            }
            if (this.currentTab !== 'chats') {
                this.chatMobileInfoOpen = false;
                this.activeChatPhone = '';
                this._stopChatPolling();
            }
            if (this.currentTab !== 'menu') {
                this.menuBulkMode = false;
                this.menuBulkSelectedIds = [];
                this.menuView = 'catalog';
            }
            this._touchLazyTabMount();
            await this.$nextTick();
            this.tabDataLoading = true;
            try {
                if (adminTabNeedsRevenueLeak(this.currentTab)) {
                    void this.loadRevenueLeak();
                }
                if (adminTabNeedsShiftState(this.currentTab)) {
                    void this.loadShiftState();
                }
                if (this.currentTab === 'dashboard') {
                    void this.refreshTaskQueueHealth();
                    if (this.dashboardTab === 'analytics') {
                        await Promise.all([this.loadAnalytics(), this.loadWaiterKpi()]);
                    } else {
                        await Promise.all([
                            this.loadDashStats(),
                            this.loadDashFunnel(),
                            this.loadAttentionSummary(),
                            this.loadRevenueLeak(),
                        ]);
                        this.deferIdleWork(async () => {
                            if (this.currentTab !== 'dashboard' || this.dashboardTab !== 'overview') return;
                            await Promise.all([this.loadDashRoiSummary(), this.loadDashActivity()]);
                        }, 1200);
                    }
                } else if (this.currentTab === 'ai_center') {
                    if (this.aiCenterTab === 'insights') {
                        await this.loadIntelligence();
                    } else if (this.aiCenterTab === 'load') {
                        await this.loadDigitalTwin();
                    } else if (this.aiCenterTab === 'os') {
                        await this.loadOsDashboard();
                    } else if (this.aiCenterTab === 'guestcare') {
                        await this.loadGuestCareReviews();
                    } else if (this.aiCenterTab === 'final_mile') {
                        await this.loadFinalMileUi();
                    } else {
                        await this.loadAiValue();
                    }
                } else if (this.currentTab === 'shift') {
                    await this.loadShiftState(true);
                    this._startShiftStateAutoRefresh();
                } else if (this.currentTab === 'inbox') {
                    if (this.effectiveStaffRole() === 'operator') {
                        void this.loadShiftState(false);
                    }
                    if (this.inboxTab === 'system') {
                        await this.loadIncidents();
                    } else {
                        await Promise.all([this.loadFailedTasks(), this.loadDashStats(), this.loadMoneyQueue()]);
                    }
                } else if (this.currentTab === 'orders') {
                    await this.loadOrders();
                } else if (this.currentTab === 'bookings') {
                    this.bookingInitWeekIfNeeded();
                    await this.loadBookingsForWeek();
                } else if (this.currentTab === 'menu') {
                    if (this.menuView === 'stoplist') {
                        // loadStopList() уже тянет полный список меню и синхронизирует this.menuItems.
                        // Двойной вызов loadMenu() создаёт гонки и лишнюю нагрузку.
                        await Promise.all([this.loadStopList(), this.loadIntegrationStatus()]);
                    } else {
                        await this.loadMenu();
                    }
                } else if (this.currentTab === 'settings') {
                    if (this.settingsTab === 'connections') {
                        await Promise.all([
                            this.loadIntegrationStatus(),
                            this.loadIikoOfficeConfig(),
                        ]);
                        void this.refreshTaskQueueHealth();
                    } else if (this.settingsTab === 'smart_sales') {
                        await this.loadUpsellRules();
                    } else if (this.settingsTab === 'team') {
                        await Promise.all([this.loadTeam(), this.loadStaffMindOnboarding()]);
                    } else if (this.settingsTab === 'health') {
                        await this.loadReadiness();
                    } else if (this.settingsTab === 'technical') {
                        if (this.isSuperadmin) {
                            await Promise.all([this.loadSettingsOrders(), this.loadSettingsEnvironment()]);
                        } else {
                            await this.loadSettingsEnvironment();
                        }
                    } else if (this.settingsTab === 'bot_test') {
                        await this.loadSetupStatus();
                    } else if (this.settingsTab === 'branding') {
                        this.syncBrandingDraftFromUser();
                    } else {
                        // restaurant
                        await this.loadOrgProfile();
                        this.armRestaurantSettingsLazyLoad();
                    }
                } else if (this.currentTab === 'chats') {
                    await this.loadChatList();
                    this._startChatPolling();
                }
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Не удалось загрузить данные вкладки. Проверьте сеть и обновите страницу.', 'Ошибка');
            }
            const dashOverviewVisible = this.currentTab === 'dashboard' && this.dashboardTab === 'overview';
            if (!dashOverviewVisible) {
                if (this._dashboardChartObserver) {
                    try { this._dashboardChartObserver.disconnect(); } catch (_e) { /* ignore */ }
                    this._dashboardChartObserver = null;
                }
                adminDestroyDashboardChart();
            }
            const dashAnalyticsVisible = this.currentTab === 'dashboard' && this.dashboardTab === 'analytics';
            if (!dashAnalyticsVisible) adminDestroyAnalyticsMainChart();
            if (!dashAnalyticsVisible) {
                this._destroyAnalyticsSparklines();
            }
            this.tabDataLoading = false;
            // После layout (fade-in / flex) — отрисовка графиков не из реактивного цикла; пауза стабилизирует размер canvas.
            await this.$nextTick();
            const tab = this.currentTab;
            setTimeout(() => {
                if (this.currentTab !== tab) return;
                if (tab === 'dashboard' && this.dashboardTab === 'overview') {
                    this.armDashboardChartLazyRender();
                }
                if (tab === 'dashboard' && this.dashboardTab === 'analytics') {
                    this._paintAnalyticsChartAfterLayout();
                }
                this._resizeVisibleCharts(tab);
                this._auditActiveTabSurface('afterLoadTabData');
            }, 100);
            [150, 400, 800].forEach((ms) => {
                setTimeout(() => this._resizeVisibleCharts(tab), ms);
            });
            this._syncShiftStatePolling();
        },

        _stopShiftHeartbeat(releaseClaim = false) {
            if (this._shiftHeartbeatTimer) {
                clearInterval(this._shiftHeartbeatTimer);
                this._shiftHeartbeatTimer = null;
            }
            if (releaseClaim) {
                this.releaseShiftFocusClaim();
            }
        },

        async sendShiftHeartbeat() {
            const focusId = this.shiftState?.focus?.id;
            const ownership = this.shiftState?.presentation?.focus_ownership
                || this.shiftState?.focus?.ownership;
            if (!focusId || ownership === 'other') return;
            const locQuery = this.locationQueryParams().toString();
            const url = locQuery ? `/api/admin/shift/heartbeat?${locQuery}` : '/api/admin/shift/heartbeat';
            try {
                const { ok, data } = await this.apiJsonResponse(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ focus_id: focusId }),
                });
                if (!ok || !data?.renewed) {
                    if (this.currentTab === 'shift') {
                        await this.loadShiftState(true);
                    }
                }
            } catch (e) {
                adminLogger.debug('[admin] sendShiftHeartbeat', e);
            }
        },

        releaseShiftFocusClaim() {
            const focusId = this.shiftState?.focus?.id;
            if (!focusId) return;
            const locQuery = this.locationQueryParams().toString();
            const url = locQuery ? `/api/admin/shift/heartbeat?${locQuery}` : '/api/admin/shift/heartbeat';
            void fetch(url, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ focus_id: focusId }),
                keepalive: true,
                credentials: 'same-origin',
            }).catch((e) => adminLogger.debug('[admin] releaseShiftFocusClaim', e));
        },

        _startShiftHeartbeat() {
            this._stopShiftHeartbeat();
            void this.sendShiftHeartbeat();
            this._shiftHeartbeatTimer = setInterval(() => {
                if (this.currentTab !== 'shift' || document.hidden) return;
                void this.sendShiftHeartbeat();
            }, 7000);
        },

        _stopShiftStateAutoRefresh() {
            this._stopShiftHeartbeat(true);
            if (this._shiftStateRefreshTimer) {
                clearInterval(this._shiftStateRefreshTimer);
                this._shiftStateRefreshTimer = null;
            }
        },

        _startShiftStateAutoRefresh() {
            this._stopShiftStateAutoRefresh();
            this._startShiftHeartbeat();
            this._shiftStateRefreshTimer = setInterval(() => {
                if (this.currentTab !== 'shift' || document.hidden) return;
                void this.loadShiftState(false);
            }, 45000);
        },

        shiftFormatWait(totalSec) {
            const sec = Math.max(0, Number(totalSec || 0));
            const m = Math.floor(sec / 60);
            const s = sec % 60;
            if (m <= 0) return `${s} сек`;
            return `${m}м ${String(s).padStart(2, '0')}с`;
        },

        shiftStateLabel(state) {
            return ({
                S0: 'Спокойно',
                S1: 'Критично — высокий риск',
                S2: 'Под нагрузкой',
                S3: 'Стабильно',
                S4: 'Ждём оплаты / есть черновики',
                S5: 'Перегруз',
            })[state] || 'Состояние уточняется';
        },

        shiftStateReasonLabel(reason) {
            return ({
                queue_spike: 'резкий рост очереди',
                extreme_risk_kzt: 'очень высокий риск потерь',
                red_chat_exists: 'есть гость без ответа в красной зоне',
                high_draft_value: 'брошенные заказы на крупную сумму',
                high_risk_kzt: 'высокий риск потери выручки',
                critical_risk: 'критичный риск',
                s1_hysteresis_latched: 'удерживаем тревогу, пока ситуация не стабилизируется',
                drafts_and_pending: 'брошенные заказы и ожидающие оплаты',
                pending_prepay_exists: 'гости ждут оплату',
                abandoned_drafts_exist: 'брошенные черновики',
                slow_chats_yellow: 'медленные ответы гостям',
                queue_busy: 'очередь под нагрузкой',
                calm_low_risk: 'спокойно, риск низкий',
                idle_fallback: 'спокойно',
            })[reason] || '';
        },

        async loadShiftState(force = false) {
            const ttlMs = 30000;
            const now = Date.now();
            if (
                !force &&
                this.shiftState &&
                this.shiftStateFetchedAt > 0 &&
                now - this.shiftStateFetchedAt < ttlMs
            ) {
                return;
            }
            this.shiftStateLoading = true;
            try {
                const { ok, data, status } = await this.apiJsonResponse(
                    `/api/admin/shift/state${this.locationQueryString('?')}`,
                );
                if (ok && data?.ok) {
                    this.shiftState = data;
                    this.shiftStateFetchedAt = Date.now();
                    this.shiftStateDegraded = false;
                    this.shiftStateLoadError = '';
                    if (this.currentTab === 'shift' && data.focus?.id) {
                        this._startShiftHeartbeat();
                    }
                    this._syncShiftPreAttention();
                } else if (this.shiftState) {
                    this.shiftStateDegraded = true;
                    this.shiftStateLoadError =
                        this.formatApiError(data?.detail) || `Ошибка загрузки (${status || '—'})`;
                }
            } catch (e) {
                adminLogger.error('[admin] loadShiftState', e);
                if (this.shiftState) {
                    this.shiftStateDegraded = true;
                    this.shiftStateLoadError = 'Нет связи с сервером — показаны последние данные';
                }
            } finally {
                this.shiftStateLoading = false;
                this._syncShiftStatePolling();
            }
        },

        async runShiftStateAction(subtype, focusId) {
            if (this.shiftActionLoading) return;
            const actionId = String(subtype || 'next');
            this.shiftActionLoading = actionId;
            this._stopShiftHeartbeat(true);
            try {
                const useGoldenFlow = actionId === 'complete' || actionId === 'skip';
                if (useGoldenFlow) {
                    await this._runShiftActionGoldenFlow(actionId, focusId);
                } else {
                    await this._runShiftActionImmediate(actionId, focusId);
                }
            } catch (e) {
                adminLogger.error('[admin] runShiftStateAction', e);
                this._abortShiftChoreo();
                void this.flashToast('Ошибка сети', 'error');
            } finally {
                this.shiftActionLoading = '';
            }
        },

        async _postShiftAction(subtype, focusId) {
            const locQuery = this.locationQueryParams().toString();
            const url = locQuery ? `/api/admin/shift/action?${locQuery}` : '/api/admin/shift/action';
            return this.apiJsonResponse(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subtype: String(subtype || 'next'),
                    focus_id: focusId || null,
                }),
            });
        },

        _applyShiftStateResponse(data, { choreo = false } = {}) {
            const prevFocusId = this.shiftState?.focus?.id;
            this.shiftState = data;
            this.shiftStateFetchedAt = Date.now();
            if (!choreo && data.live_impact?.animation) {
                void this._runImpactRevealSequence(data.live_impact);
            }
            if (data.focus?.id && data.focus.id !== prevFocusId) {
                this.shiftFocusEnterKey += 1;
            }
            this._syncShiftPreAttention();
        },

        async _runShiftActionImmediate(subtype, focusId) {
            const { ok, data } = await this._postShiftAction(subtype, focusId);
            if (ok && data?.ok) {
                this._applyShiftStateResponse(data);
            } else {
                void this.flashToast(this.formatApiError(data?.detail) || 'Не удалось выполнить действие', 'error');
            }
        },

        async _runShiftActionGoldenFlow(subtype, focusId) {
            const ms = SHIFT_CHOREO_MS;
            this._abortShiftChoreo();
            this.shiftAttentionTarget = 'card';

            const apiPromise = this._postShiftAction(subtype, focusId);

            await adminSleep(ms.pauseBeforeExit);
            this.shiftChoreoPhase = 'exiting';
            this.shiftFocusCardVisible = false;

            await adminSleep(ms.exitDuration);

            const { ok, data } = await apiPromise;
            if (!ok || !data?.ok) {
                this._abortShiftChoreo();
                void this.flashToast(this.formatApiError(data?.detail) || 'Не удалось выполнить действие', 'error');
                return;
            }

            await adminSleep(ms.impactRevealDelay);
            this.shiftChoreoPhase = 'impact';
            this.shiftAttentionTarget = 'impact';
            this.shiftChoreoImpact = data.live_impact || null;
            await this.$nextTick();
            await this._runImpactRevealSequence(this.shiftChoreoImpact);

            this.shiftChoreoPhase = 'entering';
            this.shiftAttentionTarget = 'focus';
            this.shiftChoreoImpact = null;
            this._applyShiftStateResponse(data, { choreo: true });
            this.shiftFocusCardVisible = true;

            await adminSleep(ms.focusEnterAfterPulse);
            this.shiftChoreoPhase = 'idle';
            this.shiftAttentionTarget = '';
        },

        runShiftFocusAction(action) {
            if (!action) return;
            const act = { ...action };
            if (!act.type && act.tab) act.type = 'navigate';
            if (act.type === 'api') {
                void this.runRevenueLeakAction(act).then(() => {
                    if (this.currentTab === 'shift') void this.loadShiftState(true);
                });
                return;
            }
            if (act.type === 'navigate') {
                this.runRevenueLeakAction(act);
                return;
            }
            this.runMoneyQueueAction(act);
        },

        /**
         * Диагностика «вкладка выбрана, а контент не на экране»: пишем в adminLogger только при сбое.
         * Включить подробные логи: `?admin_log=debug` или `localStorage.restomind_admin_log=debug`.
         */
        _auditActiveTabSurface(reason) {
            if (!this.authenticated) return;
            const target = adminTabSurfaceAuditTarget({
                currentTab: this.currentTab,
                dashboardTab: this.dashboardTab,
                settingsTab: this.settingsTab,
            });
            if (!target) return;
            const el = document.querySelector(target.selector);
            const payload = {
                reason: reason || 'audit',
                surface: target.key,
                selector: target.selector,
                currentTab: this.currentTab,
                dashboardTab: this.dashboardTab,
                settingsTab: this.settingsTab,
                tabDataLoading: !!this.tabDataLoading,
            };
            if (!el) {
                adminLogger.error('[admin] tab surface DOM missing', payload);
                return;
            }
            if (!adminIsDomElementVisible(el)) {
                const r = el.getBoundingClientRect();
                const cs = window.getComputedStyle(el);
                adminLogger.warn('[admin] tab surface not visible', {
                    ...payload,
                    rect: { w: r.width, h: r.height, top: r.top, left: r.left },
                    display: cs.display,
                    visibility: cs.visibility,
                    opacity: cs.opacity,
                });
                return;
            }
            adminLogger.debug('[admin] tab surface ok', payload);
        },

        isMobileViewport() {
            try {
                return window.matchMedia('(max-width: 767px)').matches;
            } catch (_e) {
                return false;
            }
        },

        deferIdleWork(fn, timeout = 1500) {
            const runner = () => {
                try {
                    const out = fn();
                    if (out && typeof out.catch === 'function') out.catch((e) => adminLogger.warn('idle work', e));
                } catch (e) {
                    adminLogger.warn('idle work', e);
                }
            };
            if (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function') {
                window.requestIdleCallback(runner, { timeout });
            } else {
                setTimeout(runner, timeout);
            }
        },

        armRestaurantSettingsLazyLoad() {
            const observe = (id, loader) => {
                const el = document.getElementById(id);
                if (!el) return;
                if (typeof IntersectionObserver === 'undefined') {
                    this.deferIdleWork(loader, 2500);
                    return;
                }
                const observer = new IntersectionObserver((entries) => {
                    if (!entries.some((entry) => entry.isIntersecting)) return;
                    try { observer.disconnect(); } catch (_e) { /* ignore */ }
                    void loader();
                }, { rootMargin: '160px 0px' });
                observer.observe(el);
            };
            observe('settings-restaurant-knowledge', async () => {
                if (this.currentTab === 'settings' && this.settingsTab === 'restaurant') await this.loadKnowledgeBase();
            });
            observe('settings-restaurant-packaging', async () => {
                if (this.currentTab !== 'settings' || this.settingsTab !== 'restaurant') return;
                await Promise.all([this.loadMenu(), this.loadPackagingRules()]);
            });
            observe('settings-restaurant-payment', async () => {
                if (this.currentTab === 'settings' && this.settingsTab === 'restaurant') await this.loadPaymentConfigs();
            });
        },

        setSettingsTab(tab) {
            const t = String(tab || '').trim();
            if (!this._adminSettingsTabIds?.has(t)) return;
            this.currentTab = 'settings';
            this.settingsTab = t;
            this.$nextTick(() => {
                const scroller = document.getElementById('admin-content-scroll');
                if (scroller) scroller.scrollTo({ top: 0, behavior: 'auto' });
            });
            this.loadTabData();
            this._schedulePushAdminHash();
        },

        async loadDashFunnel() {
            this.dashFunnelLoading = true;
            try {
                const qs = this.locationQueryString('&');
                const { ok, data } = await this.apiJsonResponse(`/api/admin/funnel?days=7${qs}`);
                if (ok) this.dashFunnel = data;
            } catch (e) {
                adminLogger.error('[admin] loadDashFunnel', e);
            } finally {
                this.dashFunnelLoading = false;
            }
        },

        async loadAiSnapshots() {
            if (this.aiSnapshotsLoading) return;
            this.aiSnapshotsLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/snapshots?limit=20');
                if (ok && data?.ok) this.aiSnapshots = data.items || [];
            } catch (_e) { /* noop */ } finally {
                this.aiSnapshotsLoading = false;
            }
        },

        async loadRevenueLeak() {
            if (this.revenueLeakLoading) return;
            this.revenueLeakLoading = true;
            try {
                const locQuery = this.locationQueryParams().toString();
                const { ok, data } = await this.apiJsonResponse(`/api/admin/intelligence/revenue-leak${locQuery ? `?${locQuery}` : ''}`);
                if (ok && data?.ok) this.revenueLeak = data;
            } catch (_e) { /* noop */ } finally {
                this.revenueLeakLoading = false;
            }
        },

        waiterKpiQueryParams() {
            const to = new Date();
            const from = new Date(to);
            from.setDate(from.getDate() - (Number(this.waiterKpiDays || 7) - 1));
            const params = new URLSearchParams({
                date_from: from.toISOString().slice(0, 10),
                date_to: to.toISOString().slice(0, 10),
            });
            const loc = this.locationQueryParams();
            if (loc.get('location_id')) params.set('location_id', loc.get('location_id'));
            return params;
        },

        waiterKpiExportHref() {
            const q = this.waiterKpiQueryParams().toString();
            return `/api/admin/analytics/waiter-kpi/export.csv${q ? `?${q}` : ''}`;
        },

        async loadWaiterKpi() {
            if (this.waiterKpiLoading) return;
            this.waiterKpiLoading = true;
            try {
                const q = this.waiterKpiQueryParams().toString();
                const [listRes, statusRes] = await Promise.all([
                    this.apiJsonResponse(`/api/admin/analytics/waiter-kpi?${q}`),
                    this.apiJsonResponse('/api/admin/analytics/waiter-kpi/sync-status'),
                ]);
                if (listRes.ok && listRes.data?.ok) {
                    this.waiterKpi = {
                        ...this.waiterKpi,
                        items: listRes.data.items || [],
                        hall_connected: !!listRes.data.hall_connected,
                        delivery_connected: !!listRes.data.delivery_connected,
                        date_from: listRes.data.date_from,
                        date_to: listRes.data.date_to,
                    };
                }
                if (statusRes.ok && statusRes.data?.ok) {
                    this.waiterKpi.last_sync = statusRes.data.last_sync || null;
                    if (listRes.data?.hall_connected == null) {
                        this.waiterKpi.hall_connected = !!statusRes.data.hall_connected;
                    }
                    if (listRes.data?.delivery_connected == null) {
                        this.waiterKpi.delivery_connected = !!statusRes.data.delivery_connected;
                    }
                }
            } catch (_e) { /* noop */ } finally {
                this.waiterKpiLoading = false;
            }
        },

        async syncWaiterKpi() {
            if (!this.canStaffManageSupply() || this.waiterKpiSyncLoading) return;
            this.waiterKpiSyncLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse(
                    '/api/admin/analytics/waiter-kpi/sync?days=1',
                    { method: 'POST' },
                );
                if (ok && data?.ok) {
                    void this.flashToast(
                        `KPI обновлены: ${Number(data.rows_upserted || 0)} записей`,
                        'success',
                        4500,
                    );
                    await this.loadWaiterKpi();
                } else {
                    void this.flashToast(this.formatApiError(data?.detail) || 'Не удалось синхронизировать KPI', 'error');
                }
            } catch (e) {
                adminLogger.error('[admin] syncWaiterKpi', e);
                void this.flashToast('Ошибка сети', 'error');
            } finally {
                this.waiterKpiSyncLoading = false;
            }
        },

        revenueLeakSurfaceClass(severity) {
            const s = String(severity || 'info');
            if (s === 'critical') return 'border-l-4 border-red-500 bg-red-50 ring-1 ring-red-200/60';
            if (s === 'warning') return 'border-l-4 border-amber-500 bg-amber-50 ring-1 ring-amber-200/60';
            return 'border-l-4 border-sky-500 bg-sky-50 ring-1 ring-sky-200/60';
        },

        revenueLeakSurfaceCountLabel(surface) {
            const count = Number(surface?.count || 0);
            const risk = Number(surface?.risk_kzt || 0);
            const unit = count === 1 ? 'заказ' : (count >= 2 && count <= 4 ? 'заказа' : 'заказов');
            if (surface?.id === 'slow_chats') {
                const conv = count === 1 ? 'диалог' : (count >= 2 && count <= 4 ? 'диалога' : 'диалогов');
                return `${count} ${conv}${risk > 0 ? ' · риск ' + this.fmt.money(risk) : ''}`;
            }
            return `${count} ${unit} · ${this.fmt.money(risk)}`;
        },

        async runRevenueLeakAction(action) {
            if (!action || this.revenueLeakActionLoading) return;
            const actionId = String(action.id || action.label || 'action');
            if (String(action.type || '') === 'api') {
                this.revenueLeakActionLoading = actionId;
                try {
                    const method = String(action.method || 'POST').toUpperCase();
                    const path = String(action.path || '');
                    if (!path) return;
                    const locQuery = this.locationQueryParams().toString();
                    const url = locQuery ? `${path}?${locQuery}` : path;
                    const { ok, data } = await this.apiJsonResponse(url, { method });
                    if (ok && data?.ok) {
                        const sent = Number(data.sent || 0);
                        void this.flashToast(
                            sent > 0 ? `Отправлено напоминаний: ${sent}` : 'Нет подходящих черновиков для возврата',
                            sent > 0 ? 'success' : 'info',
                            4500,
                        );
                        await this.loadRevenueLeak();
                    } else {
                        void this.flashToast(this.formatApiError(data?.detail) || 'Не удалось выполнить действие', 'error');
                    }
                } catch (e) {
                    adminLogger.error('[admin] runRevenueLeakAction', e);
                    void this.flashToast('Ошибка сети', 'error');
                } finally {
                    this.revenueLeakActionLoading = '';
                }
                return;
            }
            if (String(action.type || '') === 'navigate') {
                const tab = String(action.tab || 'dashboard');
                const opts = {};
                if (action.inboxTab) opts.inboxTab = action.inboxTab;
                if (action.chatPulseFilter) opts.chatPulseFilter = action.chatPulseFilter;
                if (action.orderSumMin != null) opts.orderSumMin = action.orderSumMin;
                this.navigateToTab(tab, opts);
            }
        },

        async loadDashStats() {
            const initial = !this.dashStatsLoadedOnce;
            if (initial) {
                this.dashStatsLoading = true;
            }
            try {
                const { ok, status, data } = await this.apiJsonResponse(`/api/admin/stats${this.locationQueryString('?')}`);
                if (!ok) {
                    adminLogger.warn('GET /api/admin/stats', status, data);
                    // Не затираем уже загруженные KPI/серию: второй параллельный или повторный запрос
                    // с 401/502 иначе уничтожает график (renderDashboardMiniChart: destroy → пустая серия).
                    const prev = this.dashStats;
                    const keep =
                        prev && Array.isArray(prev.daily_series) && prev.daily_series.length > 0;
                    if (!keep) {
                        this.dashStats = {
                            daily_series: [],
                            today_revenue: 0,
                            today_orders: 0,
                            menu_items: 0,
                            bookings: 0,
                        };
                    }
                    return;
                }
                this.dashStats = data;
                this.dashStatsLoadedOnce = true;
                await this.$nextTick();
                // Мини-график: scheduleDashboardChartRender вызывается из loadTabData (задержка после layout) и при смене метрики.
            } finally {
                this.dashStatsLoading = false;
            }
        },

        aiValuePeriodLabel() {
            if (this.aiValueCustom) {
                if (this.aiValueFrom && this.aiValueTo) {
                    return `${this.aiValueFrom} — ${this.aiValueTo}`;
                }
                return 'Произвольный период';
            }
            const p = String(this.aiValuePeriod || '7d');
            if (p === '30d') return '30 дней';
            if (p === '90d') return '90 дней';
            return '7 дней';
        },

        normalizeAiValuePayload(data, source) {
            const root = data && typeof data === 'object' ? data : {};
            const metrics = root.metrics && typeof root.metrics === 'object' ? root.metrics : root;
            const totals = root.totals && typeof root.totals === 'object' ? root.totals : {};
            const escalations = root.escalations && typeof root.escalations === 'object' ? root.escalations : {};
            
            const n = (v) => {
                const x = Number(v);
                return Number.isFinite(x) ? x : 0;
            };
            const nullable = (v) => {
                const x = Number(v);
                return Number.isFinite(x) ? x : null;
            };

            const orders = n(totals.orders);
            const bot_orders = n(totals.bot_orders);
            const automation_rate = orders > 0 ? Math.round((bot_orders / orders) * 100) : 0;

            return {
                source,
                period: root.period || this.aiValuePeriod,
                revenue: n(metrics.ai_revenue || metrics.ai_revenue_today || metrics.upsell_revenue || metrics.upsell_revenue_today),
                revenue_share_pct: nullable(metrics.ai_revenue_share_pct || metrics.revenue_share_pct),
                offered: n(metrics.upsell_offered || metrics.upsell_offered_today),
                accepted: n(metrics.upsell_accepted || metrics.upsell_accepted_today),
                conversion_pct: nullable(metrics.upsell_conversion_pct || metrics.conversion_pct),
                messages: n(metrics.ai_messages || metrics.ai_messages_today),
                time_saved_hours: n(metrics.ai_time_saved_hours || metrics.time_saved_hours),
                time_saved_minutes: n(metrics.ai_time_saved_minutes || metrics.time_saved_minutes),
                profit_per_saved_hour_kzt: nullable(metrics.ai_profit_per_saved_hour_kzt || metrics.profit_per_saved_hour_kzt),
                avg_check_accepted: nullable(metrics.ai_avg_check_upsell_accepted || metrics.avg_check_upsell_accepted),
                avg_check_no_offer: nullable(metrics.ai_avg_check_no_upsell_offer || metrics.avg_check_no_offer),
                daily_series: Array.isArray(root.daily_series) ? root.daily_series : (Array.isArray(metrics.daily_series) ? metrics.daily_series : []),
                
                // Расширенные поля E3
                top_upsell_items: Array.isArray(root.top_upsell_items) ? root.top_upsell_items : [],
                escalation_count: n(escalations.count),
                first_response_avg_sec: nullable(escalations.first_response_avg_sec),
                total_orders: orders,
                bot_orders: bot_orders,
                automation_rate: automation_rate,
                bot_revenue: n(totals.bot_revenue_kzt),
            };
        },

        async loadAiValue() {
            this.aiValueLoading = true;
            try {
                let url = `/api/admin/ai-value?period=${encodeURIComponent(this.aiValuePeriod || '7d')}`;
                if (this.aiValuePeriod === 'custom' && this.aiValueFrom && this.aiValueTo) {
                    url += `&date_from=${encodeURIComponent(this.aiValueFrom)}&date_to=${encodeURIComponent(this.aiValueTo)}`;
                }
                url += this.locationQueryString('&');
                const r = await this.apiJsonResponse(url);
                if (r.ok && r.data) {
                    this.aiValueData = this.normalizeAiValuePayload(r.data, 'ai-value');
                    this.aiValueSource = 'ai-value';
                    return;
                }
                const st = await this.apiJsonResponse(`/api/admin/stats${this.locationQueryString('?')}`);
                if (st.ok && st.data) {
                    this.aiValueData = this.normalizeAiValuePayload(st.data, 'stats');
                    this.aiValueSource = 'stats';
                }
            } catch (e) {
                adminLogger.error('[admin] loadAiValue', e);
            } finally {
                this.aiValueLoading = false;
            }
        },

        fmtMoney(v) {
            return adminFormat.money(v);
        },

        trendClass(pct) {
            const n = Number(pct);
            if (!Number.isFinite(n) || n === 0) return 'text-gray-500';
            return n > 0 ? 'text-emerald-600' : 'text-rose-600';
        },

        insightBorderClass(severity) {
            const s = String(severity || 'info');
            if (s === 'critical') return 'border-rose-200 bg-rose-50';
            if (s === 'warning') return 'border-amber-200 bg-amber-50';
            return 'border-blue-100 bg-blue-50';
        },

        async loadIntelligence() {
            this.intelligenceLoading = true;
            try {
                const locQs = this.locationQueryString('&');
                const [mainRes, opRes, latRes] = await Promise.all([
                    this.apiJsonResponse(`/api/admin/intelligence/overview${this.locationQueryString('?')}`),
                    this.apiJsonResponse('/api/admin/intelligence/operator-efficiency?hours=24'),
                    this.apiJsonResponse(`/api/admin/intelligence/latency?hours=24${locQs}`),
                ]);
                if (mainRes.ok) {
                    this.intelligenceData = {
                        summary: mainRes.data.summary || null,
                        insights: Array.isArray(mainRes.data.insights) ? mainRes.data.insights : [],
                        snapshot: mainRes.data.snapshot || null,
                    };
                    if (this.intelligenceData.summary?.current?.avg_check) {
                        this.digitalTwinSim.avg_check = Number(this.intelligenceData.summary.current.avg_check) || this.digitalTwinSim.avg_check;
                    }
                }
                if (opRes.ok) this.opEfficiencyData = opRes.data;
                if (latRes.ok) this.latencyData = latRes.data;
            } catch (e) {
                adminLogger.error('[admin] loadIntelligence', e);
            } finally {
                this.intelligenceLoading = false;
            }
        },

        async askIntelligence(question = null) {
            const q = String(question || this.intelligenceQuestion || '').trim();
            if (!q) return;
            this.intelligenceQuestion = q;
            this.intelligenceAsking = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        question: q,
                        conversation_id: this.intelligenceConversationId,
                    }),
                });
                if (!ok) return;
                this.intelligenceAnswer = data.answer || '';
                this.intelligenceConversationId = data.conversation_id || this.intelligenceConversationId;
                if (data.summary) {
                    this.intelligenceData.summary = data.summary;
                }
            } catch (e) {
                adminLogger.error('[admin] askIntelligence', e);
            } finally {
                this.intelligenceAsking = false;
            }
        },

        async updateInsightStatus(id, status) {
            if (!id) return;
            const { ok } = await this.apiJsonResponse(`/api/admin/intelligence/insights/${id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status }),
            });
            if (ok) {
                this.intelligenceData.insights = (this.intelligenceData.insights || []).filter((x) => x.id !== id);
            }
        },

        async loadDigitalTwin() {
            this.digitalTwinLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/intelligence/digital-twin${this.locationQueryString('?')}`);
                if (!ok) return;
                this.digitalTwin = { snapshot: data.snapshot || {} };
                if (data.snapshot?.avg_check_today) {
                    this.digitalTwinSim.avg_check = Number(data.snapshot.avg_check_today) || this.digitalTwinSim.avg_check;
                }
                const todayOrders = Number(data.snapshot?.payload?.today_orders || 0);
                if (todayOrders > 0) {
                    this.digitalTwinSim.orders_per_hour = Math.max(1, Math.round(todayOrders / 8));
                }
            } catch (e) {
                adminLogger.error('[admin] loadDigitalTwin', e);
            } finally {
                this.digitalTwinLoading = false;
            }
        },

        async runDigitalTwinSimulation() {
            this.digitalTwinSimLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/simulate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.digitalTwinSim),
                });
                if (ok) this.digitalTwinSimResult = data.result || null;
            } catch (e) {
                adminLogger.error('[admin] runDigitalTwinSimulation', e);
            } finally {
                this.digitalTwinSimLoading = false;
            }
        },

        async setAiValuePeriod(period) {
            if (period === 'custom') {
                this.aiValuePeriod = 'custom';
                this.aiValueCustom = true;
                // Не загружаем сразу, ждем "Применить" или установку дат
                return;
            }
            this.aiValuePeriod = String(period || '7d');
            this.aiValueCustom = false;
            await this.loadAiValue();
        },

        async loadDashActivity() {
            this.dashActivityLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse(`/api/admin/activity?limit=25${this.locationQueryString('&')}`);
                if (!ok) {
                    adminLogger.warn('GET /api/admin/activity', status, data);
                    return;
                }
                this.dashActivity = Array.isArray(data.items) ? data.items : [];
            } finally {
                this.dashActivityLoading = false;
            }
        },

        async loadAttentionSummary(force = false) {
            const ttlMs = 45000;
            const now = Date.now();
            if (
                !force &&
                this.attentionSummary &&
                this.attentionSummaryFetchedAt > 0 &&
                now - this.attentionSummaryFetchedAt < ttlMs
            ) {
                return;
            }
            this.attentionSummaryLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/incidents?mode=summary${this.locationQueryString('&')}`);
                if (ok && data) {
                    const prevTotal = Number(this.attentionSummary?.total_open || 0);
                    const newTotal = Number(data.total_open || 0);
                    // При появлении новых инцидентов показываем баннер снова
                    if (newTotal > prevTotal) this.systemBannerDismissed = false;
                    this.attentionSummary = data;
                    this.attentionSummaryFetchedAt = Date.now();
                    if (typeof data.is_superadmin === 'boolean') this.isSuperadmin = data.is_superadmin;
                }
            } catch (e) {
                adminLogger.error('[admin] loadAttentionSummary', e);
            } finally {
                this.attentionSummaryLoading = false;
            }
        },

        /** Классы кнопок блока «Сейчас» по серьёзности группы инцидентов */
        heroActionButtonClass(severity) {
            const s = String(severity || '').toLowerCase();
            if (s === 'critical') {
                return 'border-red-300 bg-red-50 text-red-900 hover:bg-red-100 ring-1 ring-red-900/5';
            }
            if (s === 'warning') {
                return 'border-amber-300 bg-amber-50 text-amber-950 hover:bg-amber-100 ring-1 ring-amber-900/5';
            }
            return 'border-slate-200 bg-white text-slate-800 hover:bg-slate-50 ring-1 ring-slate-900/5';
        },

        async loadReadiness() {
            this.readinessLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/readiness');
                this.readinessPayload = ok && data ? data : adminDefaultReadinessPayload();
            } catch (e) {
                adminLogger.error('[admin] loadReadiness', e);
            } finally {
                this.readinessLoading = false;
            }
        },

        async toggleOrderTimeline() {
            this.orderTimelineExpanded = !this.orderTimelineExpanded;
            if (this.orderTimelineExpanded && !this.orderTimeline.length && !this.orderTimelineLoading) {
                void this.loadOrderTimeline(this.selectedOrder?.id);
            }
        },

        async loadOrderTimeline(orderId) {
            const id = Number(orderId);
            if (!Number.isFinite(id) || id < 1) return;
            this.orderTimelineLoading = true;
            this.orderTimeline = [];
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/orders/${id}/timeline`);
                if (ok && data && Array.isArray(data.events)) this.orderTimeline = data.events;
            } catch (e) {
                adminLogger.error('[admin] loadOrderTimeline', e);
            } finally {
                this.orderTimelineLoading = false;
            }
        },

        heroAttentionGo(target) {
            this.incidentGo(target);
        },

        async openTopAction(action) {
            if (!action) return;
            if (action.id) {
                try {
                    await this.apiJsonResponse(
                        `/api/admin/intelligence/recommendations/${action.id}`,
                        {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ status: 'viewed' }),
                        },
                    );
                } catch (e) {
                    adminLogger.warn('[admin] openTopAction patch viewed', e);
                }
            }
            if (action.target && typeof action.target === 'object') {
                this.incidentGo(action.target);
                return;
            }
            this.navigateToTab('ai_center', { aiCenterTab: 'insights' });
        },

        async copyReadinessLink(kind) {
            const p = this.readinessPayload && this.readinessPayload.links ? this.readinessPayload.links : {};
            let s = '';
            if (kind === 'payment_webhook') s = p.payment_webhook_url || '';
            else if (kind === 'whatsapp') s = p.whatsapp_webhook_url || '';
            else if (kind === 'public_base') s = p.public_base_url || '';
            if (!s) {
                void this.showUiAlert('Нет URL — задайте PUBLIC_BASE_URL на сервере.', 'Внимание');
                return;
            }
            try {
                await navigator.clipboard.writeText(s);
                void this.showUiAlert('Скопировано в буфер обмена', 'Готово');
            } catch {
                void this.showUiAlert('Не удалось скопировать', 'Ошибка');
            }
        },

        async loadIncidents() {
            this.incidentsLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/incidents?mode=full');
                if (!ok) {
                    adminLogger.warn('GET /api/admin/incidents', status, data);
                    if (!this.incidentsLoadedOnce) {
                        this.incidents = {
                            groups: [],
                            summary: { critical: 0, warning: 0, info: 0, restricted: 0 },
                            total_open: 0,
                            severity: 'ok',
                            restricted_count: 0,
                            generated_at: null,
                            is_superadmin: this.isSuperadmin,
                        };
                    }
                    return;
                }
                this.incidents = {
                    groups: Array.isArray(data.groups) ? data.groups : [],
                    summary: data.summary || { critical: 0, warning: 0, info: 0, restricted: 0 },
                    total_open: Number(data.total_open || 0),
                    severity: data.severity || 'ok',
                    restricted_count: Number(data.restricted_count || 0),
                    generated_at: data.generated_at || null,
                    is_superadmin: !!data.is_superadmin,
                    superadmin_only: Array.isArray(data.superadmin_only) ? data.superadmin_only : [],
                };
                this.isSuperadmin = !!data.is_superadmin;
                this.incidentsLoadedOnce = true;
            } catch (e) {
                adminLogger.error('[admin] loadIncidents', e);
            } finally {
                this.incidentsLoading = false;
            }
        },

        incidentGo(target) {
            const t = target && typeof target === 'object' ? target : {};
            const tab = String(t.tab || '').trim();
            if (!tab) return;
            if (tab === 'settings') {
                this.navigateToTab('settings', {
                    settingsTab: t.settingsTab || t.settings_tab || 'connections',
                });
                return;
            }
            if (tab === 'chats' && t.phone) {
                const phone = String(t.phone);
                this.navigateToTab('chats');
                setTimeout(() => this.selectChat(phone), 80);
                return;
            }
            if (tab === 'incidents') {
                this.navigateToTab('inbox', { inboxTab: 'system' });
                return;
            }
            if (tab === 'operator_queue' || tab === 'errors') {
                this.navigateToTab('inbox', { inboxTab: 'clients' });
                return;
            }
            if (tab === 'analytics') {
                this.navigateToTab('dashboard', { dashboardTab: 'analytics' });
                return;
            }
            if (tab === 'ai_value') {
                this.navigateToTab('ai_center', { aiCenterTab: 'value' });
                return;
            }
            if (tab === 'intelligence') {
                this.navigateToTab('ai_center', { aiCenterTab: 'insights' });
                return;
            }
            if (tab === 'digital_twin') {
                this.navigateToTab('ai_center', { aiCenterTab: 'load' });
                return;
            }
            if (tab === 'menu') {
                this.navigateToTab('menu', { menuView: t.menuView || 'catalog' });
                return;
            }
            if (tab === 'dashboard') {
                this.navigateToTab('dashboard', { dashboardTab: t.dashboardTab || 'overview' });
                return;
            }
            if (tab === 'ai_center') {
                this.navigateToTab('ai_center', { aiCenterTab: t.aiCenterTab || 'insights' });
                return;
            }
            this.navigateToTab(tab);
        },

        async loadOsDashboard() {
            if (this.osDashboardLoading) return;
            this.osDashboardLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/intelligence/os-dashboard${this.locationQueryString('?')}`);
                if (ok && data?.ok) this.osDashboardData = data;
            } catch (_e) { /* noop */ } finally {
                this.osDashboardLoading = false;
            }
            if (!this.auditLog.length) void this.loadAuditLog();
        },

        async loadFinalMileUi() {
            await Promise.all([
                this.loadDailyDigestPreview(),
                this.loadSupplyMind(),
                this.loadInventorySyncStatus(),
                this.loadVoiceAiStatus(),
                this.loadVoiceCallLogs(),
            ]);
        },

        async loadDailyDigestPreview() {
            if (this.dailyDigestLoading) return;
            this.dailyDigestLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/daily-os-digest/preview');
                if (ok && data?.item) this.dailyDigestPreview = data.item;
            } catch (_e) { /* noop */ } finally {
                this.dailyDigestLoading = false;
            }
        },

        async loadSupplyMind() {
            if (this.supplyMindLoading) return;
            this.supplyMindLoading = true;
            try {
                const [draftsRes, alertsRes] = await Promise.all([
                    this.apiJsonResponse('/api/admin/intelligence/supplymind/drafts?limit=10'),
                    this.apiJsonResponse(`/api/admin/intelligence/inventory/stock-alerts${this.locationQueryString('?')}`),
                ]);
                if (draftsRes.ok && draftsRes.data?.items) this.supplyMindDrafts = draftsRes.data.items;
                if (alertsRes.ok && alertsRes.data?.items) this.supplyMindAlerts = alertsRes.data.items;
            } catch (_e) { /* noop */ } finally {
                this.supplyMindLoading = false;
            }
        },

        async loadInventorySyncStatus() {
            if (this.inventorySyncLoading) return;
            this.inventorySyncLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/inventory/sync-status');
                if (ok && data) this.inventorySyncStatus = data;
            } catch (_e) { /* noop */ } finally {
                this.inventorySyncLoading = false;
            }
        },

        async runInventorySyncIiko() {
            if (!this.canStaffManageSupply()) {
                void this.showUiAlert(this.staffRbacHint('manager') || 'Недостаточно прав', 'SupplyMind');
                return;
            }
            if (this.inventorySyncRunning) return;
            this.inventorySyncRunning = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/inventory/sync-iiko', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: '{}',
                });
                if (ok && data?.ok) {
                    this.setToast(`iiko Office: обновлено ${data.updated ?? 0} остатков`);
                    await Promise.all([
                        this.loadInventorySyncStatus(),
                        this.loadSupplyMind(),
                    ]);
                } else {
                    void this.showUiAlert(this.formatApiError(data?.detail) || 'Не удалось синхронизировать остатки iiko Office', 'SupplyMind');
                }
            } catch (e) {
                adminLogger.error('[admin] inventory iiko sync', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'SupplyMind');
            } finally {
                this.inventorySyncRunning = false;
            }
        },

        inventorySyncStatusLabel() {
            const st = this.inventorySyncStatus;
            if (!st) return this.inventorySyncLoading ? 'Проверяем…' : 'Статус неизвестен';
            if (!st.iiko_office_configured) return 'iiko Office не настроен';
            const last = st.last_inventory_sync || {};
            if (!last.at) return 'Готов к первой синхронизации';
            return last.ok ? `Последняя синхронизация: ${this.fmt.date(last.at)}` : `Ошибка синхронизации: ${last.error || 'см. логи'}`;
        },

        async createSupplyMindDraft() {
            if (!this.canStaffManageSupply()) {
                void this.showUiAlert(this.staffRbacHint('manager') || 'Недостаточно прав', 'SupplyMind');
                return;
            }
            if (this.supplyMindCreateLoading) return;
            this.supplyMindCreateLoading = true;
            try {
                const body = {
                    cover_days: Number(this.supplyMindCoverDays) || 7,
                    location_id: this.selectedLocationId ? Number(this.selectedLocationId) : null,
                };
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/supplymind/drafts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (ok && data?.item) {
                    this.supplyMindDrafts = [data.item, ...(this.supplyMindDrafts || [])];
                    this.setToast('SupplyMind создал чеклист закупки');
                } else {
                    void this.showUiAlert(this.formatApiError(data?.detail) || 'Не удалось создать чеклист закупки', 'SupplyMind');
                }
            } finally {
                this.supplyMindCreateLoading = false;
            }
        },

        supplyMindStatusLabel(status) {
            const labels = {
                draft: 'Черновик',
                approved: 'Утверждён',
                completed: 'Завершён',
                cancelled: 'Отменён',
            };
            return labels[status] || status || '—';
        },

        toggleSupplyMindDraftExpand(draftId) {
            const id = Number(draftId);
            if (!Number.isFinite(id) || id < 1) return;
            this.supplyMindExpandedDraftId = this.supplyMindExpandedDraftId === id ? null : id;
        },

        supplyMindItemCheckKey(draftId, idx) {
            return `${Number(draftId)}:${Number(idx)}`;
        },

        isSupplyMindItemChecked(draftId, idx) {
            const draft = (this.supplyMindDrafts || []).find((d) => Number(d?.id) === Number(draftId));
            const item = Array.isArray(draft?.items) ? draft.items[Number(idx)] : null;
            if (item && typeof item === 'object' && typeof item.checked === 'boolean') {
                return item.checked;
            }
            return false;
        },

        async toggleSupplyMindItem(draftId, idx) {
            if (!this.canStaffManageSupply()) return;
            const id = Number(draftId);
            const i = Number(idx);
            if (!Number.isFinite(id) || !Number.isFinite(i) || i < 0) return;
            const draft = (this.supplyMindDrafts || []).find((d) => Number(d?.id) === id);
            if (!draft || !Array.isArray(draft.items) || !draft.items[i]) return;
            if (draft.status === 'completed' || draft.status === 'cancelled') return;

            const nextChecked = !this.isSupplyMindItemChecked(id, i);
            const patchKey = this.supplyMindItemCheckKey(id, i);
            if (this.supplyMindItemPatchLoading === patchKey) return;
            this.supplyMindItemPatchLoading = patchKey;

            const items = draft.items.map((row, rowIdx) => {
                if (rowIdx !== i) return row;
                return { ...(row || {}), checked: nextChecked };
            });
            this.supplyMindDrafts = (this.supplyMindDrafts || []).map((d) =>
                Number(d?.id) === id ? { ...d, items } : d,
            );

            try {
                const { ok, data } = await this.apiJsonResponse(
                    `/api/admin/intelligence/supplymind/drafts/${id}`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ items: [{ idx: i, checked: nextChecked }] }),
                    },
                );
                if (ok && data?.item) {
                    this.supplyMindDrafts = (this.supplyMindDrafts || []).map((d) =>
                        Number(d?.id) === id ? data.item : d,
                    );
                } else {
                    void this.showUiAlert(
                        this.formatApiError(data?.detail) || 'Не удалось сохранить отметку',
                        'SupplyMind',
                    );
                    await this.loadSupplyMind();
                }
            } catch (e) {
                adminLogger.error('[admin] supplymind item patch', e);
                await this.loadSupplyMind();
            } finally {
                this.supplyMindItemPatchLoading = null;
            }
        },

        supplyMindCheckedCount(draft) {
            const items = Array.isArray(draft?.items) ? draft.items : [];
            if (!items.length) return 0;
            return items.reduce((n, item) => n + (item?.checked ? 1 : 0), 0);
        },

        supplyMindDraftProgressPct(draft) {
            const total = Array.isArray(draft?.items) ? draft.items.length : 0;
            if (!total) return 0;
            return Math.round((this.supplyMindCheckedCount(draft) / total) * 100);
        },

        async updateSupplyMindDraft(draftId, status) {
            if (!this.canStaffManageSupply()) {
                void this.showUiAlert(this.staffRbacHint('manager') || 'Недостаточно прав', 'SupplyMind');
                return;
            }
            if (this.supplyMindUpdateLoading) return;
            this.supplyMindUpdateLoading = draftId;
            try {
                const { ok, data } = await this.apiJsonResponse(
                    `/api/admin/intelligence/supplymind/drafts/${draftId}`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status }),
                    },
                );
                if (ok && data?.item) {
                    this.supplyMindDrafts = (this.supplyMindDrafts || []).map((d) =>
                        d.id === draftId ? data.item : d,
                    );
                    this.setToast('Статус чеклиста обновлён');
                } else {
                    void this.showUiAlert(
                        this.formatApiError(data?.detail) || 'Не удалось обновить чеклист',
                        'SupplyMind',
                    );
                }
            } finally {
                this.supplyMindUpdateLoading = null;
            }
        },

        async exportSupplyMindDraft(draftId) {
            if (!this.canStaffManageSupply()) {
                void this.showUiAlert(this.staffRbacHint('manager') || 'Недостаточно прав', 'SupplyMind');
                return;
            }
            if (this.supplyMindExportLoading) return;
            this.supplyMindExportLoading = draftId;
            try {
                const res = await this.apiFetch(
                    `/api/admin/intelligence/supplymind/drafts/${draftId}/export?format=csv`,
                );
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    void this.showUiAlert(this.formatApiError(data?.detail) || 'Ошибка выгрузки CSV', 'SupplyMind');
                    return;
                }
                const blob = await res.blob();
                const u = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = u;
                a.download = `supplymind_checklist_${draftId}.csv`;
                a.click();
                URL.revokeObjectURL(u);
            } catch (e) {
                adminLogger.error('[admin] supplymind export csv', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'SupplyMind');
            } finally {
                this.supplyMindExportLoading = null;
            }
        },

        async loadVoiceAiStatus() {
            if (this.voiceAiLoading) return;
            this.voiceAiLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/voice/status');
                if (ok && data?.item) {
                    this.voiceAiStatus = data.item;
                    this.voiceAiEnabledDraft = !!data.item.enabled;
                    this.voiceAiModeDraft = data.item.mode === 'realtime' ? 'realtime' : 'stt_fallback';
                }
            } catch (_e) { /* noop */ } finally {
                this.voiceAiLoading = false;
            }
        },

        async saveVoiceAiConfig() {
            if (!this.canStaffAdminOnly()) {
                void this.showUiAlert(this.staffRbacHint('admin') || 'Недостаточно прав', 'Голосовой бот');
                return;
            }
            if (this.voiceAiSaving) return;
            this.voiceAiSaving = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/intelligence/voice/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enabled: !!this.voiceAiEnabledDraft,
                        mode: this.voiceAiModeDraft === 'realtime' ? 'realtime' : 'stt_fallback',
                    }),
                });
                if (ok && data?.item) {
                    this.voiceAiStatus = data.item;
                    this.voiceAiEnabledDraft = !!data.item.enabled;
                    this.voiceAiModeDraft = data.item.mode === 'realtime' ? 'realtime' : 'stt_fallback';
                    this.setToast('Настройки голосового бота сохранены');
                } else if (status === 403) {
                    void this.showUiAlert(
                        this.formatApiError(data?.detail) || 'Только администратор может менять голосовой бот',
                        'Голосовой бот',
                    );
                } else {
                    void this.showUiAlert(this.formatApiError(data?.detail) || 'Не удалось сохранить настройки голосового бота', 'Голосовой бот');
                }
            } finally {
                this.voiceAiSaving = false;
            }
        },

        async loadOsDecisionFeed() {
            return this.loadAuditLog();
        },

        async applyAutopilotPricingBulk() {
            if (this.applyPricingBulkLoading) return;
            const ap = this.osDashboardData?.autopilot_pricing;
            if (!ap || ap.tactic === 'stable' || !ap.price_adj_pct) {
                void this.showUiAlert('Нет активной ценовой рекомендации для применения.', 'Автопилот');
                return;
            }
            const okConfirm = window.confirm(
                `Применить корректировку цен ${ap.price_adj_pct > 0 ? '+' : ''}${ap.price_adj_pct}% ко всем активным позициям?`,
            );
            if (!okConfirm) return;
            this.applyPricingBulkLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse(
                    '/api/admin/intelligence/apply-pricing-signal',
                    { method: 'POST', headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ price_adj_pct: ap.price_adj_pct }) },
                );
                if (!ok) {
                    void this.showUiAlert((data && data.detail) || 'Не удалось применить цены', 'Автопилот');
                    return;
                }
                void this.showUiAlert(
                    `Обновлено позиций: ${data.items_updated || 0}`,
                    'Автопилот',
                );
                await this.loadOsDashboard();
                void this.loadAuditLog();
            } finally {
                this.applyPricingBulkLoading = false;
            }
        },

        async loadGuestCareReviews() {
            if (this.guestCareLoading) return;
            this.guestCareLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/reviews/external');
                if (ok && data?.items) this.guestCareReviews = data.items;
                if (ok && data?.sync_meta) this.guestCareSyncMeta = data.sync_meta;
            } catch (_e) { /* noop */ } finally {
                this.guestCareLoading = false;
            }
        },

        async syncGuestCareReviews() {
            if (this.guestCareSyncLoading) return;
            this.guestCareSyncLoading = true;
            this.guestCareSyncMessage = '';
            try {
                const { ok, data } = await this.apiJsonResponse(
                    '/api/admin/intelligence/reviews/external/sync',
                    { method: 'POST' },
                );
                if (!ok) {
                    this.guestCareSyncMessage = (data && data.detail) || 'Не удалось синхронизировать отзывы';
                    return;
                }
                if (data?.items) this.guestCareReviews = data.items;
                const stats = data?.stats || {};
                if (stats.sync_meta) this.guestCareSyncMeta = stats.sync_meta;
                else if (data?.stats?.sources) {
                    this.guestCareSyncMeta = {
                        last_at: new Date().toISOString(),
                        inserted: stats.inserted,
                        updated: stats.updated,
                        parsed: stats.parsed,
                    };
                }
                if (stats.skipped && stats.reason === 'no_review_urls') {
                    this.guestCareSyncMessage = 'Укажите ссылку 2GIS в настройках ресторана (review_url_2gis).';
                    return;
                }
                if (stats.skipped && stats.reason === 'google_manual_only') {
                    this.guestCareSyncMessage = 'Авто-sync только для 2GIS. Для Google используйте ручной импорт по URL ниже.';
                    return;
                }
                const inserted = Number(stats.inserted || 0);
                const updated = Number(stats.updated || 0);
                const parsed = Number(stats.parsed || 0);
                this.guestCareSyncMessage = `Готово: найдено ${parsed}, новых ${inserted}, обновлено ${updated}.`;
                if (Array.isArray(stats.errors) && stats.errors.length) {
                    this.guestCareSyncMessage += ` Ошибки: ${stats.errors.length}.`;
                }
                const lims = stats.sync_meta?.limitations || [];
                if (Array.isArray(lims) && lims.length) {
                    this.guestCareSyncMessage += ' См. ограничения ниже.';
                }
            } catch (_e) {
                this.guestCareSyncMessage = 'Ошибка синхронизации отзывов';
            } finally {
                this.guestCareSyncLoading = false;
            }
        },

        async importGuestCareReview() {
            const url = (this.guestCareImportUrl || '').trim();
            if (!url) return;
            const { ok, data } = await this.apiJsonResponse(
                '/api/admin/intelligence/reviews/external/import',
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url }),
                },
            );
            if (ok && data?.item) {
                this.guestCareReviews = [data.item, ...(this.guestCareReviews || [])];
                this.guestCareImportUrl = '';
            }
        },

        async draftGuestCareReply(reviewId) {
            const { ok, data } = await this.apiJsonResponse(
                `/api/admin/intelligence/reviews/external/${encodeURIComponent(reviewId)}/reply-draft`,
                { method: 'POST' },
            );
            if (ok && data?.reply_draft) {
                this.guestCareReviews = (this.guestCareReviews || []).map((r) => (
                    String(r.id) === String(reviewId)
                        ? { ...r, reply_draft: data.reply_draft }
                        : r
                ));
            }
        },

        async loadAuditLog({ more = false } = {}) {
            if (this.auditLogLoading) return;
            this.auditLogLoading = true;
            try {
                const limit = more ? 50 : 20;
                const { ok, data } = await this.apiJsonResponse(
                    `/api/admin/intelligence/audit-log?limit=${limit}`
                );
                if (ok && data?.entries) {
                    this.auditLog = more
                        ? [...(this.auditLog || []), ...data.entries]
                        : data.entries;
                }
            } catch (_e) { /* noop */ } finally {
                this.auditLogLoading = false;
            }
        },

        async loadTraceTimeline(traceId) {
            const tid = String(traceId ?? this.traceTimelineQuery ?? '').trim();
            if (tid.length < 8) {
                void this.showUiAlert('Введите trace_id (минимум 8 символов)', 'Control Plane');
                return;
            }
            if (this.traceTimelineLoading) return;
            this.traceTimelineLoading = true;
            this.traceTimelineQuery = tid;
            try {
                const { ok, data } = await this.apiJsonResponse(
                    `/api/admin/intelligence/trace-timeline?trace_id=${encodeURIComponent(tid)}&limit=100`,
                );
                if (ok) {
                    this.traceTimeline = {
                        trace_id: data.trace_id || tid,
                        entries: Array.isArray(data.entries) ? data.entries : [],
                        total: Number(data.total) || 0,
                    };
                } else {
                    this.traceTimeline = { trace_id: tid, entries: [], total: 0 };
                    void this.showUiAlert(
                        this.formatApiError(data?.detail) || 'Не удалось загрузить цепочку trace',
                        'Control Plane',
                    );
                }
            } catch (e) {
                adminLogger.error('[admin] trace timeline', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Control Plane');
            } finally {
                this.traceTimelineLoading = false;
            }
        },

        traceTimelineEntryLabel(entry) {
            const row = entry && typeof entry === 'object' ? entry : {};
            if (row.kind === 'chat_log') {
                const role = row.role === 'user' ? 'Гость' : (row.role === 'assistant' ? 'ИИ' : 'Оператор');
                const preview = String(row.content || '').slice(0, 80);
                return `${role}: ${preview || '—'}`;
            }
            const typ = row.type || row.action || 'событие';
            const actor = row.actor ? ` (${row.actor})` : '';
            return `${typ}${actor}`;
        },

        traceTimelineEntryBadgeClass(entry) {
            const row = entry && typeof entry === 'object' ? entry : {};
            if (row.kind === 'chat_log') return 'ds-status-surface ds-status-ai';
            const typ = String(row.type || '');
            if (typ.includes('failed') || typ.includes('error')) return 'ds-status-surface ds-status-danger';
            if (typ.includes('warn') || typ.includes('slow')) return 'ds-status-surface ds-status-warn';
            return 'ds-status-surface ds-status-inactive';
        },

        openTraceTimeline(traceId) {
            const tid = String(traceId || '').trim();
            if (!tid) return;
            this.navigateToTab('ai_center', { aiCenterTab: 'os' });
            void this.loadOsDashboard().then(() => this.loadTraceTimeline(tid));
        },

        openActiveChatTraceTimeline() {
            if (!this.activeChatTraceId) return;
            this.openTraceTimeline(this.activeChatTraceId);
        },

        _osActionLabel(action) {
            const labels = {
                'order.created': 'Новый заказ',
                'order.confirmed': 'Заказ подтверждён',
                'order.cancelled': 'Заказ отменён',
                'booking.created': 'Новое бронирование',
                'booking.confirmed': 'Бронирование подтверждено',
                'booking.cancelled': 'Бронирование отменено',
                'payment.completed': 'Оплата получена',
                'payment.failed': 'Ошибка оплаты',
                'payment.expired': 'Оплата истекла',
                'ai.escalated': 'Бот позвал оператора',
                'ai.dialog.started': 'Новый диалог с гостем',
                'operator.took_over': 'Оператор подключился',
                'system.sla_violated': 'Просрочен ответ',
                'system.pricing_adjusted': 'Цены скорректированы',
                'system.healing_wa_sent': 'Напоминание об оплате в WhatsApp',
                'integration.iiko.failed': 'Ошибка отправки в iiko',
                'integration.whatsapp.failed': 'Сбой доставки WhatsApp',
                'cancellation_surge': 'Рост отмен',
                'escalation_spike': 'Много запросов оператору',
                'payment_spike': 'Всплеск ошибок оплаты',
                'ai_message_drop': 'Падение ответов ИИ',
            };
            if (labels[action]) return labels[action];
            return String(action || '')
                .replace(/\./g, ' · ')
                .replace(/_/g, ' ');
        },

        async loadDashRoiSummary() {
            this.dashRoiLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse(`/api/admin/roi/today${this.locationQueryString('?')}`);
                if (!ok) {
                    adminLogger.warn('GET /api/admin/roi/today', status, data);
                    this.dashRoiSummary = null;
                    return;
                }
                this.dashRoiSummary = data || adminDefaultDashRoiSummary();
            } finally {
                this.dashRoiLoading = false;
            }
        },

        /**
         * Chart.js после x-show — контейнер мог быть 0×0; ждём 2× rAF перед созданием.
         * Возвращает Promise, чтобы loadDashStats дождался отрисовки (иначе resize в loadTabData иногда раньше графика).
         */
        scheduleDashboardChartRender() {
            return new Promise((resolve) => {
                requestAnimationFrame(() => {
                    requestAnimationFrame(async () => {
                        try {
                            await this.renderDashboardMiniChart();
                            const canvas = document.getElementById('dashboardHeroChart');
                            const parent = canvas?.parentElement;
                            if (charts.dashboard && parent) {
                                this._attachChartLayoutFix(charts.dashboard, parent);
                            }
                        } finally {
                            resolve();
                        }
                    });
                });
            });
        },

        armDashboardChartLazyRender() {
            if (this.currentTab !== 'dashboard' || this.dashboardTab !== 'overview' || charts.dashboard) return;
            // На mobile график ниже первого решения владельца; не грузим Chart.js в initial render.
            if (this.isMobileViewport()) return;
            const canvas = document.getElementById('dashboardHeroChart');
            if (!canvas) return;
            if (this._dashboardChartObserver) {
                try { this._dashboardChartObserver.disconnect(); } catch (_e) { /* ignore */ }
                this._dashboardChartObserver = null;
            }
            if (typeof IntersectionObserver === 'undefined') {
                if (!this.isMobileViewport()) void this.scheduleDashboardChartRender();
                return;
            }
            this._dashboardChartObserver = new IntersectionObserver((entries) => {
                if (!entries.some((entry) => entry.isIntersecting)) return;
                try { this._dashboardChartObserver?.disconnect(); } catch (_e) { /* ignore */ }
                this._dashboardChartObserver = null;
                void this.scheduleDashboardChartRender();
            }, { rootMargin: '220px 0px' });
            this._dashboardChartObserver.observe(canvas);
        },

        /** После изменения заказов: перерисовать мини-график, если открыт дашборд. */
        async syncDashboardChartIfVisible() {
            await this.$nextTick();
            if (this.currentTab === 'dashboard' && this.dashboardTab === 'overview') {
                if (charts.dashboard) {
                    await this.scheduleDashboardChartRender();
                } else {
                    this.armDashboardChartLazyRender();
                }
            }
        },

        /**
         * Несколько WS-событий подряд (массовое удаление) не должны дергать stats десятки раз.
         */
        scheduleDashStatsRefreshDebounced() {
            if (this._dashOrderChangeTimer) {
                clearTimeout(this._dashOrderChangeTimer);
            }
            this._dashOrderChangeTimer = setTimeout(async () => {
                this._dashOrderChangeTimer = null;
                try {
                    await this.loadDashStats();
                    await this.syncDashboardChartIfVisible();
                } catch (e) {
                    adminLogger.error('[admin] scheduleDashStatsRefreshDebounced', e);
                }
            }, 350);
        },

        /** Мгновенно убрать заказы из локальных списков (до прихода ответа loadOrders). */
        removeOrderIdsFromLocalState(ids) {
            const s = new Set(
                (ids || []).map((x) => Number(x)).filter((n) => Number.isFinite(n)),
            );
            if (!s.size) return;
            this.orders = this.orders.filter((o) => !s.has(Number(o.id)));
            this.settingsOrdersList = this.settingsOrdersList.filter((o) => !s.has(Number(o.id)));
            this.settingsSelectedOrderIds = this.settingsSelectedOrderIds.filter(
                (id) => !s.has(Number(id)),
            );
            if (this.selectedOrder && s.has(Number(this.selectedOrder.id))) {
                this.showOrderModal = false;
                this.selectedOrder = null;
            }
        },

        toggleAnalyticsDaySort(col) {
            if (this.analyticsDaySort === col) {
                this.analyticsDayDir = this.analyticsDayDir === 'asc' ? 'desc' : 'asc';
            } else {
                this.analyticsDaySort = col;
                this.analyticsDayDir = col === 'date' ? 'desc' : 'desc';
            }
        },

        toggleOrdersSort(column) {
            if (this.ordersSort.column === column) {
                this.ordersSort = { column, dir: this.ordersSort.dir === 'asc' ? 'desc' : 'asc' };
            } else {
                const dir = column === 'client' || column === 'status' || column === 'order_type' ? 'asc' : 'desc';
                this.ordersSort = { column, dir };
            }
        },

        async loadOrders() {
            const reqId = ++this._ordersLoadSeq;
            this.ordersLoading = true;
            this.ordersLoadError = '';
            const p = new URLSearchParams();
            const tableMode = this.ordersView === 'table';
            const size = tableMode ? Number(this.ordersSize || 50) : 500;
            const page = tableMode ? Number(this.ordersPage || 1) : 1;
            p.set('page', String(Number.isFinite(page) && page > 0 ? page : 1));
            p.set('size', String(Number.isFinite(size) && size > 0 ? Math.min(500, size) : 50));
            if (this.orderFilter) p.set('status', this.orderFilter);
            const locParams = this.locationQueryParams();
            for (const [k, v] of locParams.entries()) p.set(k, v);
            const q = (this.orderSearchQ || '').trim();
            if (q) p.set('q', q);
            const smin = this.orderSumMin;
            const smax = this.orderSumMax;
            if (smin !== '' && smin != null && !Number.isNaN(Number(smin))) {
                p.set('sum_min', String(Number(smin)));
            }
            if (smax !== '' && smax != null && !Number.isNaN(Number(smax))) {
                p.set('sum_max', String(Number(smax)));
            }
            try {
                const { ok, status, data } = await this.apiJsonResponse(`/api/admin/orders?${p.toString()}`);
                if (reqId !== this._ordersLoadSeq) return;
                if (!ok) {
                    this.ordersLoadError = this.formatApiError(data?.detail) || `Не удалось загрузить заказы (${status})`;
                    void this.showUiAlert(this.ordersLoadError, 'Ошибка');
                    return;
                }
                const incoming = Array.isArray(data.items) ? data.items : (Array.isArray(data.orders) ? data.orders : []);
                this.ordersTotal = Number(data.total ?? incoming.length) || 0;
                this.ordersPages = Number(data.pages ?? 1) || 1;
                this.ordersPage = Number(data.page ?? this.ordersPage) || 1;
                this.ordersHasMore = !!data.has_more;
                const merged = new Map((this.orders || []).map((o) => [Number(o.id), o]));
                for (const next of incoming) {
                    const id = Number(next?.id);
                    if (!Number.isFinite(id)) continue;
                    const prev = merged.get(id);
                    if (!prev || Number(next?.row_version || 0) >= Number(prev?.row_version || 0)) {
                        merged.set(id, next);
                    }
                }
                this.orders = Array.from(merged.values());
                if (!this._ordersViewAutoSet && this.ordersView === 'kanban' && this.ordersTotal === 0 && incoming.length === 0) {
                    this.ordersView = 'table';
                    this._ordersViewAutoSet = true;
                }
            } catch (e) {
                if (reqId !== this._ordersLoadSeq) return;
                adminLogger.error('[admin] loadOrders', e);
                this.ordersLoadError = 'Ошибка сети при загрузке заказов';
                void this.showUiAlert(this.ordersLoadError, 'Ошибка');
            } finally {
                if (reqId === this._ordersLoadSeq) this.ordersLoading = false;
            }
        },

        ordersPrevPage() {
            if (this.ordersView !== 'table') return;
            const p = Number(this.ordersPage || 1);
            if (p <= 1) return;
            this.ordersPage = p - 1;
            window.scrollTo({ top: 0 });
            void this.loadOrders();
        },

        ordersNextPage() {
            if (this.ordersView !== 'table') return;
            const p = Number(this.ordersPage || 1);
            const pages = Number(this.ordersPages || 1);
            if (p >= pages) return;
            this.ordersPage = p + 1;
            window.scrollTo({ top: 0 });
            void this.loadOrders();
        },

        async loadFailedTasks() {
            this.failedTasksLoading = true;
            try {
                const q = new URLSearchParams();
                if (this.failedTasksFilter === 'open') q.set('resolved', 'false');
                else if (this.failedTasksFilter === 'resolved') q.set('resolved', 'true');
                const ph = (this.failedTasksPhone || '').trim();
                if (ph) q.set('phone', ph);
                const { ok, status, data } = await this.apiJsonResponse(`/api/admin/failed-tasks?${q.toString()}`);
                if (!ok) {
                    adminLogger.warn('[admin] loadFailedTasks', status, data);
                    return;
                }
                this.failedTasks = data.tasks || [];
                this.failedTasksTotal = data.total ?? (data.tasks || []).length;
            } finally {
                this.failedTasksLoading = false;
            }
        },

        async loadTeam() {
            this.teamLoading = true;
            this.teamError = '';
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/staff');
                if (!ok) {
                    this.teamUsers = [];
                    this.teamError = this.formatApiError(data.detail) || 'Не удалось загрузить список сотрудников';
                    return;
                }
                this.teamUsers = Array.isArray(data.users) ? data.users : [];
            } finally {
                this.teamLoading = false;
            }
        },

        async loadStaffMindOnboarding() {
            if (this.staffMindLoading) return;
            this.staffMindLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/staffmind/onboarding?limit=20');
                if (ok && data?.ok) this.staffMindSessions = Array.isArray(data.items) ? data.items : [];
            } catch (_e) { /* noop */ } finally {
                this.staffMindLoading = false;
            }
        },

        staffMindTrackerMeta(session) {
            const s = (session && typeof session === 'object') ? session : {};
            const progress = (s.progress && typeof s.progress === 'object') ? s.progress : {};
            const topics = Array.isArray(progress.completed_topics) ? progress.completed_topics : [];
            const questionsAsked = Number.isFinite(Number(s.questions_asked))
                ? Number(s.questions_asked)
                : (Number.isFinite(Number(progress.questions_asked))
                    ? Number(progress.questions_asked)
                    : (s.last_question ? 1 : 0));
            const testPassed = s.test_passed === true
                || progress.test_passed === true
                || String(s.status || '').toLowerCase() === 'completed';
            const currentStep = Number(s.current_step) || topics.length || 0;
            const stepTarget = Number.isFinite(Number(s.step_target))
                ? Number(s.step_target)
                : (Number(progress.step_target) || Math.max(5, currentStep, topics.length));
            return {
                currentStep,
                stepTarget,
                topicsCount: topics.length,
                questionsAsked,
                testPassed,
                topics,
            };
        },

        staffMindStepProgressPct(session) {
            const meta = this.staffMindTrackerMeta(session);
            if (!meta.stepTarget) return 0;
            return Math.min(100, Math.round((meta.currentStep / meta.stepTarget) * 100));
        },

        async startStaffMindOnboarding() {
            if (!this.canStaffStartStaffMind()) {
                void this.showUiAlert(this.staffRbacHint('manager') || 'Недостаточно прав', 'Обучение сотрудников');
                return;
            }
            const phone = String(this.staffMindPhone || '').trim();
            if (!phone) {
                this.teamError = 'Введите телефон сотрудника для обучения';
                return;
            }
            if (this.staffMindStartLoading) return;
            this.staffMindStartLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/staffmind/onboarding', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        phone,
                        role: String(this.staffMindRole || 'staff').trim() || 'staff',
                    }),
                });
                if (ok && data?.item) {
                    this.staffMindSessions = [data.item, ...(this.staffMindSessions || [])];
                    this.staffMindPhone = '';
                    this.staffMindRole = 'staff';
                    this.setToast('Обучение сотрудника запущено');
                } else {
                    this.teamError = this.formatApiError(data?.detail) || 'Не удалось запустить обучение';
                }
            } finally {
                this.staffMindStartLoading = false;
            }
        },

        async askStaffMind(sessionId) {
            if (!this.canStaffStartStaffMind()) {
                void this.showUiAlert(this.staffRbacHint('manager') || 'Недостаточно прав', 'Обучение сотрудников');
                return;
            }
            const id = Number(sessionId);
            if (!Number.isFinite(id) || id < 1) return;
            const question = String(this.staffMindQuestionById?.[id] || '').trim();
            if (!question) return;
            this.staffMindAskLoadingId = id;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/intelligence/staffmind/onboarding/${id}/message`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question }),
                });
                if (ok && data?.item) {
                    this.staffMindSessions = (this.staffMindSessions || []).map((s) => Number(s.id) === id ? data.item : s);
                    this.staffMindQuestionById = { ...(this.staffMindQuestionById || {}), [id]: '' };
                } else {
                    this.teamError = this.formatApiError(data?.detail) || 'Не удалось получить ответ';
                }
            } finally {
                this.staffMindAskLoadingId = null;
            }
        },

        teamLocationOptions() {
            const locs = (this.userData && Array.isArray(this.userData.available_locations))
                ? this.userData.available_locations
                : [];
            return locs.map((l) => ({ id: Number(l.id), name: String(l.name || l.slug || l.id) }));
        },

        openTeamEdit(u) {
            if (!u || !u.id) return;
            this.teamEditId = Number(u.id);
            this.teamEditRole = String(u.role || 'operator');
            const meta = (u.role_metadata && typeof u.role_metadata === 'object') ? u.role_metadata : {};
            this.teamEditMetaTitle = String(meta.title || '');
            this.teamEditMetaDepartment = String(meta.department || '');
            const ids = Array.isArray(u.assigned_location_ids) ? u.assigned_location_ids.map((x) => Number(x)) : [];
            this.teamEditLocationIds = ids.filter((x) => Number.isFinite(x) && x > 0);
        },

        cancelTeamEdit() {
            this.teamEditId = null;
            this.teamEditSaving = false;
        },

        async saveTeamMemberMeta() {
            const id = Number(this.teamEditId);
            if (!Number.isFinite(id) || id < 1) return;
            this.teamEditSaving = true;
            this.teamError = '';
            try {
                const payload = {
                    role: String(this.teamEditRole || 'operator'),
                    assigned_location_ids: (this.teamEditLocationIds || []).map((x) => Number(x)).filter((x) => x > 0),
                    role_metadata: {
                        title: String(this.teamEditMetaTitle || '').trim(),
                        department: String(this.teamEditMetaDepartment || '').trim(),
                    },
                };
                const { ok, data } = await this.apiJsonResponse(`/api/admin/staff/${id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!ok) {
                    this.teamError = this.formatApiError(data?.detail) || 'Не удалось сохранить настройки сотрудника';
                    return;
                }
                this.teamEditId = null;
                await this.loadTeam();
                this.setToast('Настройки сотрудника сохранены');
            } finally {
                this.teamEditSaving = false;
            }
        },

        async createTeamMember() {
            this.teamError = '';
            this.teamTempPassword = '';
            const email = (this.teamNewEmail || '').trim();
            if (!email) {
                this.teamError = 'Введите email сотрудника';
                return;
            }
            this.teamCreateLoading = true;
            try {
                const body = {
                    email,
                    role: (this.teamNewRole || 'operator'),
                    password: (this.teamNewPassword || ''),
                };
                const locIds = (this.teamNewLocationIds || []).map((x) => Number(x)).filter((x) => x > 0);
                if (locIds.length) body.assigned_location_ids = locIds;
                const title = String(this.teamNewMetaTitle || '').trim();
                const dept = String(this.teamNewMetaDepartment || '').trim();
                if (title || dept) {
                    body.role_metadata = { title, department: dept };
                }
                const { ok, data } = await this.apiJsonResponse('/api/admin/staff', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
                if (!ok) {
                    this.teamError = this.formatApiError(data.detail) || 'Не удалось добавить сотрудника';
                    return;
                }
                if (data.temp_password) {
                    this.teamTempPassword = String(data.temp_password);
                }
                this.teamNewEmail = '';
                this.teamNewPassword = '';
                this.teamNewMetaTitle = '';
                this.teamNewMetaDepartment = '';
                this.teamNewLocationIds = [];
                await this.loadTeam();
            } finally {
                this.teamCreateLoading = false;
            }
        },

        async setFailedTaskResolved(task, resolved) {
            try {
                const { ok } = await this.apiJsonResponse(`/api/admin/failed-tasks/${task.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ resolved }),
                });
                if (ok) {
                    await this.loadFailedTasks();
                    await this.loadDashStats();
                }
            } catch (e) {
                adminLogger.error(e);
            }
        },

        async retryFailedTask(task) {
            const id = Number(task && task.id);
            if (!id) return;
            this.failedTaskRetryingId = id;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/failed-tasks/${id}/retry`, {
                    method: 'POST',
                });
                if (!ok) {
                    void this.showUiAlert(this.formatApiError(data.detail || data) || 'Не удалось поставить задачу на повтор', 'Ошибка');
                    return;
                }
                void this.showUiAlert('Повторная обработка поставлена в очередь.', 'Готово');
                await this.loadFailedTasks();
                await this.loadDashStats();
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.failedTaskRetryingId = null;
            }
        },

        initOrderRebuildFromSelected() {
            this.orderRebuildError = '';
            const items = this.selectedOrder?.items?.items;
            if (!Array.isArray(items) || items.length === 0) {
                this.orderRebuildDraftJson = '[]';
                return;
            }
            const lines = items.map((it) => ({
                name: it.name,
                quantity: it.quantity,
                iiko_item_id: it.iiko_id || '',
                packaging_plov_1kg: it.packaging_plov_1kg || '',
                exclude_ingredients: it.exclude_ingredients || [],
            }));
            this.orderRebuildDraftJson = JSON.stringify(lines, null, 2);
        },

        async submitOrderRebuildDraft() {
            if (!this.selectedOrder || this.selectedOrder.status !== 'draft') return;
            this.orderRebuildError = '';
            let food_lines;
            try {
                food_lines = JSON.parse(this.orderRebuildDraftJson || '[]');
            } catch {
                this.orderRebuildError = 'Некорректный JSON: проверьте кавычки, запятые и скобки. Можно проверить текст во внешнем валидаторе JSON. Поле не очищено — исправьте и отправьте снова.';
                return;
            }
            if (!Array.isArray(food_lines) || food_lines.length === 0) {
                this.orderRebuildError = 'Нужен непустой массив позиций';
                return;
            }
            await this.submitOrderRebuild({ food_lines });
        },

        async submitOrderRebuild({ food_lines, closeComposition = false } = {}) {
            if (!this.selectedOrder) return;
            const st = String(this.selectedOrder.status || '').toLowerCase();
            if (st !== 'draft' && st !== 'confirmed') return;

            this.orderRebuildLoading = true;
            this.orderRebuildError = '';
            try {
                const { ok, data } = await this.apiJsonResponse(
                    `/api/admin/orders/${this.selectedOrder.id}/rebuild-draft`,
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            food_lines,
                            expected_version: this.selectedOrder.row_version,
                        }),
                    },
                );
                if (!ok) {
                    const d = data.detail;
                    this.orderRebuildError = typeof d === 'string' ? d : JSON.stringify(d || data);
                    return;
                }
                if (data.payment_split_warning) {
                    this.orderRebuildError = '';
                }
                await this.loadOrders();
                const updated = this.orders.find((o) => o.id === this.selectedOrder.id);
                if (updated) {
                    this.selectedOrder = updated;
                    this.initOrderRebuildFromSelected();
                    this.initOrderCompositionLinesFromSelected();
                    this.syncOrderPaymentFormFromSelected();
                    if (closeComposition) this.orderCompositionOpen = false;
                }
                await this.loadDashStats();
            } catch {
                this.orderRebuildError = 'Ошибка сети';
            } finally {
                this.orderRebuildLoading = false;
            }
        },

        async loadBookings() {
            this.bookingInitWeekIfNeeded();
            await this.loadBookingsForWeek();
        },

        async loadBookingsForWeek() {
            this.bookingInitWeekIfNeeded();
            this.bookingsLoadError = '';
            this.bookingsLoading = true;
            const anchor = this._bookingParseIso(this.bookingWeekAnchor);
            const end = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate() + 6);
            const dateFrom = this._bookingIsoDate(anchor);
            const dateTo = this._bookingIsoDate(end);
            const qs = new URLSearchParams({
                date_from: dateFrom,
                date_to: dateTo,
                limit: '200',
            });
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/bookings?${qs.toString()}`);
                if (!ok) {
                    this.bookings = [];
                    this.bookingsLoadError = this.formatApiError(data) || 'Не удалось загрузить брони';
                    return;
                }
                this.bookings = data.bookings || [];
            } catch {
                this.bookings = [];
                this.bookingsLoadError = 'Ошибка сети';
            } finally {
                this.bookingsLoading = false;
            }
        },

        async loadMenu() {
            this.menuLoadError = '';
            const { ok, status, data } = await this.apiJsonResponse('/api/admin/menu?available_only=false');
            if (!ok) {
                this.menuItems = [];
                this.menuCategories = [];
                this.menuViewRevision += 1;
                this.menuLoadError = typeof data.detail === 'string'
                    ? data.detail
                    : (status === 401 ? 'Сессия истекла — войдите снова' : 'Не удалось загрузить меню');
                adminLogger.error('Меню: ошибка API', status, data);
                return;
            }
            this.menuItems = data.items || [];
            const cats = [
                ...new Set(
                    this.menuItems.map((i) => {
                        const c = i.category;
                        return c && String(c).trim() ? String(c).trim() : 'Прочее';
                    }),
                ),
            ];
            const ord = this.menuCategoryOrder;
            this.menuCategories = cats.sort((a, b) => {
                const ia = ord.indexOf(a);
                const ib = ord.indexOf(b);
                if (ia === -1 && ib === -1) return a.localeCompare(b, 'ru');
                if (ia === -1) return 1;
                if (ib === -1) return -1;
                return ia - ib;
            });
            this.menuViewRevision += 1;
        },

        /** Список заказов для вкладки «Настройки» (до 100 шт.). */
        async loadSettingsOrders() {
            this.settingsOrdersLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/orders?limit=100&offset=0');
                if (!ok) {
                    this.settingsOrdersList = [];
                    return;
                }
                this.settingsOrdersList = Array.isArray(data.orders) ? data.orders : [];
            } catch (e) {
                adminLogger.error('[admin] loadSettingsOrders', e);
                this.settingsOrdersList = [];
            } finally {
                this.settingsOrdersLoading = false;
            }
        },

        async loadSettingsEnvironment() {
            this.settingsEnvLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/settings/environment');
                this.settingsEnv = ok && data ? data : adminDefaultSettingsEnv();
            } catch (e) {
                adminLogger.error('[admin] loadSettingsEnvironment', e);
                this.settingsEnv = adminDefaultSettingsEnv();
            } finally {
                this.settingsEnvLoading = false;
            }
        },

        async settingsPurgeRedisSession() {
            if (!this.ensureSuperadminAction()) return;
            const phone = (this.settingsRedisPhone || '').trim();
            if (!phone || this.settingsRedisPurgeLoading) return;
            const { ok } = await this.openUiConfirm({
                message: `Сбросить Redis/in-memory сессию для ${phone}? История диалога в памяти будет очищена.`,
                danger: true,
            });
            if (!ok) return;
            this.settingsRedisPurgeLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/settings/redis-purge-phone', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: true, phone }),
                });
                if (!ok) {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Ошибка', 'Ошибка');
                    return;
                }
                this.flashToast(`Сессия сброшена: ${phone}`, 'info', 3500);
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsRedisPurgeLoading = false;
            }
        },

        _settingsExportQuery() {
            const p = new URLSearchParams();
            if (this.settingsExportDateFrom) p.set('date_from', this.settingsExportDateFrom);
            if (this.settingsExportDateTo) p.set('date_to', this.settingsExportDateTo);
            const q = p.toString();
            return q ? `?${q}` : '';
        },

        async _settingsDownloadCsv(path, filename) {
            if (this.settingsExportLoading) return;
            this.settingsExportLoading = true;
            try {
                const res = await this.apiFetch(path + this._settingsExportQuery());
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Ошибка выгрузки', 'Ошибка');
                    return;
                }
                const blob = await res.blob();
                const u = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = u;
                a.download = filename;
                a.click();
                URL.revokeObjectURL(u);
            } catch (e) {
                adminLogger.error('[admin] export csv', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsExportLoading = false;
            }
        },

        async settingsDownloadOrdersCsv() {
            await this._settingsDownloadCsv('/api/admin/export/orders', 'restomind_orders_export.csv');
        },

        async settingsDownloadChatsCsv() {
            await this._settingsDownloadCsv('/api/admin/export/chats', 'restomind_chats_export.csv');
        },

        async settingsBulkCancelOrders() {
            if (!this.ensureSuperadminAction()) return;
            const ids = [...new Set(this.settingsSelectedOrderIds.map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0))];
            if (!ids.length || this.settingsBulkCancelLoading) return;
            const { ok } = await this.openUiConfirm({
                message: `Отменить ${ids.length} заказ(ов)? Статус станет «отменён», строки в БД сохранятся.`,
                danger: true,
            });
            if (!ok) return;
            this.settingsBulkCancelLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/orders/bulk-cancel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: true, order_ids: ids }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data), 'Ошибка');
                    return;
                }
                this.settingsSelectedOrderIds = [];
                this.flashToast(
                    `Отменено заказов: ${data.cancelled ?? 0}; уже были отменены: ${data.skipped_already_cancelled ?? 0}`,
                    'success',
                    4500,
                );
                await Promise.all([this.loadSettingsOrders(), this.loadOrders(), this.loadDashStats(), this.loadSettingsEnvironment()]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                adminLogger.error('[admin] settingsBulkCancelOrders', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsBulkCancelLoading = false;
            }
        },

        async settingsRunChatRetention() {
            if (!this.ensureSuperadminAction()) return;
            if (this.settingsRetentionRunLoading) return;
            const { ok } = await this.openUiConfirm({
                message: 'Удалить старые записи chat_logs по политике CHAT_LOG_RETENTION_DAYS?',
                danger: true,
            });
            if (!ok) return;
            this.settingsRetentionRunLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/settings/chat-logs/run-retention', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: true }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Ошибка', 'Ошибка');
                    return;
                }
                this.flashToast(`Удалено записей чата: ${data.deleted ?? 0}`, 'success', 4000);
                await this.loadSettingsEnvironment();
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsRetentionRunLoading = false;
            }
        },

        async settingsBulkDeleteOrders() {
            if (!this.ensureSuperadminAction()) return;
            const ids = [...new Set(this.settingsSelectedOrderIds.map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0))];
            if (!ids.length) return;
            await this.confirmAndDeleteOrders(ids, 'settings_bulk');
        },

        async settingsClearMenuOnly() {
            if (!this.ensureSuperadminAction()) return;
            if (this.settingsMenuClearLoading) return;
            let r = await this.openUiConfirm({
                message: 'Удалить все позиции меню из базы? Заказы в БД не удаляются.',
                danger: true,
            });
            if (!r.ok) return;
            r = await this.openUiConfirm({
                message: 'Подтвердите ещё раз: каталог будет пуст до синхронизации с iiko.',
                danger: true,
            });
            if (!r.ok) return;
            this.settingsMenuClearLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/menu/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: true }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Не удалось очистить меню', 'Ошибка');
                    return;
                }
                this.flashToast(
                    data.deleted != null ? `Меню: удалено позиций ${data.deleted}` : 'Меню очищено',
                    'warning',
                    4000,
                );
                await Promise.all([this.loadMenu(), this.loadDashStats()]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsMenuClearLoading = false;
            }
        },

        async settingsClearMenuAndStopSnapshot() {
            if (!this.ensureSuperadminAction()) return;
            if (this.settingsMenuStopClearLoading) return;
            let r = await this.openUiConfirm({
                message: 'Удалить все позиции меню, привязанные к этому филиалу? (Глобальный индикатор интеграций и legacy-меню без филиала не затрагиваются.)',
                danger: true,
            });
            if (!r.ok) return;
            r = await this.openUiConfirm({
                message: 'Последнее подтверждение: номенклатура будет пуста.',
                danger: true,
            });
            if (!r.ok) return;
            this.settingsMenuStopClearLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/settings/clear-menu-and-stop-snapshot', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: true }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Ошибка очистки', 'Ошибка');
                    return;
                }
                this.flashToast(`Меню филиала очищено (${data.menu_items_deleted ?? 0} поз.)`, 'warning', 4500);
                await Promise.all([
                    this.loadMenu(),
                    this.loadDashStats(),
                    this.loadStopList(),
                    this.loadIntegrationStatus(),
                ]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                adminLogger.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsMenuStopClearLoading = false;
            }
        },

        async confirmSettingsPurgeOperational() {
            if (!this.ensureSuperadminAction()) return;
            if (this.settingsPurgeLoading) return;
            this.settingsPurgeError = '';
            this.settingsPurgeLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/settings/purge-operational-data', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        confirm: true,
                        phrase: this.settingsPurgePhrase.trim(),
                    }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    this.settingsPurgeError = typeof data.detail === 'string' ? data.detail : 'Ошибка';
                    return;
                }
                this.settingsPurgeModalOpen = false;
                this.settingsPurgePhrase = '';
                this.settingsPurgeAck = false;
                this.flashToast(
                    `Сброс: заказов ${data.orders_deleted ?? 0}, броней ${data.bookings_deleted ?? 0}, сообщений чата ${data.chat_logs_deleted ?? 0}`,
                    'warning',
                    5000,
                );
                await Promise.all([
                    this.loadSettingsOrders(),
                    this.loadOrders(),
                    this.loadBookings(),
                    this.loadDashStats(),
                    this.loadIntegrationStatus(),
                ]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                adminLogger.error(e);
                this.settingsPurgeError = 'Ошибка сети';
            } finally {
                this.settingsPurgeLoading = false;
            }
        },

        async clearAllMenuFromDb() {
            if (!this.ensureSuperadminAction()) return;
            const n = this.menuItems.length;
            if (!n || this.menuClearLoading) return;
            let r = await this.openUiConfirm({
                message: `Удалить все ${n} позиций меню из базы? Старые заказы не удалятся.`,
                danger: true,
            });
            if (!r.ok) return;
            r = await this.openUiConfirm({
                message: 'Последнее подтверждение: номенклатура будет пуста до синхронизации с iiko.',
                danger: true,
            });
            if (!r.ok) return;
            this.menuClearLoading = true;
            try {
                const res = await this.apiFetch('/api/admin/menu/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ confirm: true }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Не удалось очистить меню', 'Ошибка');
                    return;
                }
                this.flashToast(
                    data.deleted != null ? `Удалено позиций: ${data.deleted}` : 'Меню очищено',
                    'success',
                    3200,
                );
                await this.loadMenu();
                await this.loadDashStats();
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                adminLogger.error('[admin] clearAllMenuFromDb', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.menuClearLoading = false;
            }
        },

        openMenuCreate() {
            this.menuEditForm = {
                id: null,
                name: '',
                category: this.menuCategoryFilter || '',
                description: '',
                tags: '',
                price: 0,
                is_available: true,
                image_url: '',
                portion_kind: 'single',
                serves_min: 1,
                serves_max: 1,
                allergens: '',
                ingredients_summary: '',
                dietary_tags: '',
                upsell_pairs: '',
            };
            this.menuEditOpen = true;
        },

        openMenuEdit(item) {
            if (!item || item.id == null) return;
            if (this.menuBulkSuppressOpen) {
                this.menuBulkSuppressOpen = false;
                return;
            }
            this.menuEditForm = {
                id: item.id,
                name: item.name || '',
                category: item.category || '',
                description: item.description || '',
                tags: item.tags || '',
                price: Number(item.price) || 0,
                is_available: !!item.is_available,
                image_url: item.image_url || '',
                portion_kind: (item.portion_kind === 'shareable' ? 'shareable' : 'single'),
                serves_min: Math.max(1, Number(item.serves_min) || 1),
                serves_max: Math.max(1, Number(item.serves_max) || 1),
                allergens: item.allergens || '',
                ingredients_summary: item.ingredients_summary || '',
                dietary_tags: item.dietary_tags || '',
                upsell_pairs: item.upsell_pairs || '',
            };
            this.menuEditOpen = true;
        },

        closeMenuEdit() {
            if (this.menuEditSaving) return;
            this.menuEditOpen = false;
        },

        async saveMenuItem() {
            const f = this.menuEditForm;
            const name = String(f.name || '').trim();
            if (!name) return;
            this.menuEditSaving = true;
            try {
                const smin = Math.max(1, Math.min(99, Number(f.serves_min) || 1));
                let smax = Math.max(1, Math.min(99, Number(f.serves_max) || 1));
                if (smax < smin) smax = smin;
                const payload = {
                    name,
                    category: String(f.category || '').trim(),
                    description: String(f.description || '').trim(),
                    tags: String(f.tags || '').trim(),
                    price: Math.max(0, Number(f.price) || 0),
                    is_available: !!f.is_available,
                    image_url: String(f.image_url || '').trim() || null,
                    portion_kind: (f.portion_kind === 'shareable' ? 'shareable' : 'single'),
                    serves_min: smin,
                    serves_max: smax,
                    allergens: String(f.allergens || '').trim(),
                    ingredients_summary: String(f.ingredients_summary || '').trim(),
                    dietary_tags: String(f.dietary_tags || '').trim(),
                    upsell_pairs: String(f.upsell_pairs || '').trim(),
                };
                let r;
                if (f.id == null) {
                    r = await this.apiJsonResponse('/api/admin/menu', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                } else {
                    r = await this.apiJsonResponse(`/api/admin/menu/${f.id}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                }
                const { ok, data } = r;
                if (ok) {
                    this.menuEditOpen = false;
                    await this.loadMenu();
                    this.flashToast(f.id == null ? 'Позиция добавлена' : 'Позиция сохранена', 'success', 3000);
                } else {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Не удалось сохранить', 'Ошибка');
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.menuEditSaving = false;
            }
        },

        async deleteMenuItemFromModal() {
            const id = this.menuEditForm.id;
            if (id == null) return;
            const { ok } = await this.openUiConfirm({
                message: 'Удалить эту позицию из меню? Старые заказы в истории не изменятся.',
                danger: true,
            });
            if (!ok) return;
            this.menuEditSaving = true;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/menu/${id}`, { method: 'DELETE' });
                if (ok) {
                    this.menuEditOpen = false;
                    await this.loadMenu();
                    this.flashToast('Позиция удалена', 'success', 3000);
                } else {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Не удалось удалить', 'Ошибка');
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.menuEditSaving = false;
            }
        },

        getMenuCategoryCount(cat) {
            return this.menuItems.filter((i) => {
                const c = i.category && String(i.category).trim() ? String(i.category).trim() : 'Прочее';
                return c === cat;
            }).length;
        },

        menuBulkIsSelected(id) {
            return this.menuBulkSelectedIds.includes(id);
        },

        menuBulkToggleId(id) {
            const i = this.menuBulkSelectedIds.indexOf(id);
            if (i >= 0) {
                this.menuBulkSelectedIds.splice(i, 1);
            } else {
                this.menuBulkSelectedIds.push(id);
            }
        },

        toggleMenuBulkMode() {
            this.menuBulkTouchEnd();
            this.menuBulkMode = !this.menuBulkMode;
            if (!this.menuBulkMode) {
                this.menuBulkSelectedIds = [];
                this.menuBulkTargetCategory = '';
            }
        },

        // bulk-stoplist: long-press на карточке → режим выбора + позиция (mobile)
        menuBulkTouchStart(item) {
            if (!item || item.id == null) return;
            this.menuBulkTouchEnd();
            this.menuBulkLongPressTimer = setTimeout(() => {
                this.menuBulkLongPressTimer = null;
                this.menuBulkSuppressOpen = true;
                if (!this.menuBulkMode) this.toggleMenuBulkMode();
                if (!this.menuBulkIsSelected(item.id)) this.menuBulkToggleId(item.id);
                try {
                    if (navigator.vibrate) navigator.vibrate(12);
                } catch (_) {
                    /* ignore */
                }
            }, 520);
        },

        menuBulkTouchEnd() {
            if (this.menuBulkLongPressTimer) {
                clearTimeout(this.menuBulkLongPressTimer);
                this.menuBulkLongPressTimer = null;
            }
        },

        menuBulkSelectFiltered() {
            for (const row of this.menuFilteredItems) {
                if (row.id != null && !this.menuBulkSelectedIds.includes(row.id)) {
                    this.menuBulkSelectedIds.push(row.id);
                }
            }
        },

        menuBulkClearSelection() {
            this.menuBulkSelectedIds = [];
        },

        // bulk-stoplist: POST /api/admin/menu/bulk-stoplist
        async menuBulkApplyAvailability(available) {
            const ids = [...this.menuBulkSelectedIds];
            if (!ids.length || this.menuBulkSaving) return;
            this.menuBulkSaving = true;
            try {
                const action = available ? 'unstop' : 'stop';
                const { ok, data } = await this.apiJsonResponse('/api/admin/menu/bulk-stoplist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action, item_ids: ids }),
                });
                await this.loadMenu();
                this.menuBulkSelectedIds = [];
                if (!ok) {
                    void this.showUiAlert(
                        typeof data.detail === 'string' ? data.detail : 'Не удалось обновить позиции',
                        'Ошибка',
                    );
                    return;
                }
                const nFail = Array.isArray(data.failed) ? data.failed.length : 0;
                const nOk = typeof data.updated === 'number' ? data.updated : 0;
                if (nFail > 0) {
                    void this.showUiAlert(
                        `Обновлено: ${nOk}, пропущено (нет в филиале): ${nFail}`,
                        'Частично',
                    );
                }
                if (nOk > 0) {
                    this.flashToast(
                        available ? 'Выбранные позиции в продаже' : 'Выбранные позиции в стопе',
                        'success',
                        2800,
                    );
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.menuBulkSaving = false;
            }
        },

        // bulk-stoplist: смена раздела (строка category)
        async menuBulkApplyCategory() {
            const ids = [...this.menuBulkSelectedIds];
            const cat = String(this.menuBulkTargetCategory || '').trim();
            if (!ids.length || !cat || this.menuBulkSaving) return;
            this.menuBulkSaving = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/menu/bulk-stoplist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: 'set_category', item_ids: ids, category: cat }),
                });
                await this.loadMenu();
                this.menuBulkSelectedIds = [];
                this.menuBulkTargetCategory = '';
                if (!ok) {
                    void this.showUiAlert(
                        typeof data.detail === 'string' ? data.detail : 'Не удалось сменить раздел',
                        'Ошибка',
                    );
                    return;
                }
                const nFail = Array.isArray(data.failed) ? data.failed.length : 0;
                const nOk = typeof data.updated === 'number' ? data.updated : 0;
                if (nFail > 0) {
                    void this.showUiAlert(
                        `Перенесено: ${nOk}, пропущено: ${nFail}`,
                        'Частично',
                    );
                }
                if (nOk > 0) {
                    this.flashToast('Раздел обновлён', 'success', 2600);
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.menuBulkSaving = false;
            }
        },

        async toggleMenuAvailability(item) {
            if (!item || item.id == null) return;
            try {
                const { ok, data } = await this.apiJsonResponse(`/api/admin/menu/${item.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ is_available: !item.is_available }),
                });
                if (ok && data.item) {
                    Object.assign(item, data.item);
                    this.menuViewRevision += 1;
                } else if (!ok) {
                    void this.showUiAlert(typeof data.detail === 'string' ? data.detail : 'Не удалось обновить', 'Ошибка');
                }
            } catch {
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            }
        },

        async loadAnalytics() {
            this.analyticsLoading = true;
            let url = `/api/admin/analytics?period=${this.analyticsPeriod}`;
            if (this.analyticsPeriod === 'custom' && this.analyticsFrom && this.analyticsTo) {
                url += `&date_from=${this.analyticsFrom}&date_to=${this.analyticsTo}`;
            }
            url += this.locationQueryString('&');
            try {
                const { ok, status, data: raw } = await this.apiJsonResponse(url);
                if (!ok) {
                    this.analyticsData = {
                        daily: [],
                        current: {},
                        previous: {},
                        ai: {},
                        top_items: [],
                        changes: {},
                    };
                    adminLogger.warn('GET /api/admin/analytics', status, raw);
                    this.analyticsDailyDataRev = (this.analyticsDailyDataRev || 0) + 1;
                    return;
                }
                this.analyticsData = raw;
                this.analyticsDailyDataRev = (this.analyticsDailyDataRev || 0) + 1;
            } finally {
                this.analyticsLoading = false;
            }
        },

        /** Перерисовка графика аналитики после стабилизации layout (вызов из loadTabData или reloadAnalyticsForUi). */
        async _paintAnalyticsChartAfterLayout() {
            try {
                await this.renderChart();
                const canvas = document.getElementById('revenueChart');
                const parent = canvas?.parentElement;
                if (charts.analytics && parent) {
                    this._attachChartLayoutFix(charts.analytics, parent);
                }
            } catch (e) {
                adminLogger.warn('analytics chart paint', e);
            }
        },

        /** Смена периода на вкладке «Аналитика»: данные + один отложенный рендер графика. */
        async reloadAnalyticsForUi() {
            await Promise.all([this.loadAnalytics(), this.loadWaiterKpi(), this.loadSalesHeatmapIiko()]);
            await this.$nextTick();
            setTimeout(() => {
                if (this.currentTab !== 'dashboard' || this.dashboardTab !== 'analytics') return;
                this._paintAnalyticsChartAfterLayout();
            }, 100);
        },

        async loadSalesHeatmapIiko() {
            if (this.analyticsDensity !== 'advanced' && this.canToggleAnalyticsDensity()) return;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/analytics/sales-heatmap?days=7&source=iiko');
                if (ok && data) {
                    this.analyticsData = { ...(this.analyticsData || {}), sales_heatmap_iiko: data };
                }
            } catch (_e) {
                /* optional iiko block */
            }
        },

        async renderChart() {
            const canvas = document.getElementById('revenueChart');
            if (!canvas) return;
            let daily = this.analyticsData.daily || [];
            // Раньше график "просто рисовался по нулям", даже когда данных нет.
            // Если daily пустой — рисуем 1 точку с нулём, чтобы вкладка не выглядела "сломанной".
            if (!Array.isArray(daily) || daily.length === 0) {
                const today = new Date().toISOString().slice(0, 10);
                daily = [{ date: today, revenue: 0, ai_profit: 0 }];
            }
            await adminEnsureChartJs();

            const ctx = canvas.getContext('2d');
            adminDestroyAnalyticsMainChart();
            const chartFont = adminChartJsCommonFont();

            const labels = daily.map((d) => {
                const dd = adminFormat._parseDateInput(d.date);
                if (!dd) return '—';
                return dd.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' });
            });

            const revenueSeries = daily.map((d) => Number(d.revenue) || 0);
            const aiSeries = daily.map((d) => Number(d.ai_profit) || 0);
            const self = this;
            const externalTooltipPlugin = {
                id: 'rmExternalTooltip',
                afterDraw(chart) {
                    const tooltip = chart.tooltip;
                    const parent = chart.canvas?.parentElement;
                    if (!parent) return;
                    parent.style.position = 'relative';

                    let el = parent.querySelector('.rm-tooltip');
                    if (!el) {
                        el = document.createElement('div');
                        el.className = 'rm-tooltip pointer-events-none absolute z-10 opacity-0 transition-opacity duration-75';
                        el.innerHTML = '<div class="rounded-xl bg-slate-900 text-white shadow-lg ring-1 ring-white/10 px-3 py-2 text-xs"></div>';
                        parent.appendChild(el);
                    }

                    const body = el.querySelector('div');
                    if (!body) return;

                    if (!tooltip || tooltip.opacity === 0 || !tooltip.dataPoints || tooltip.dataPoints.length === 0) {
                        el.style.opacity = '0';
                        return;
                    }

                    const dp = tooltip.dataPoints[0];
                    const idx = Number(dp.dataIndex);
                    const dateLabel = labels[idx] || '—';
                    const rev = revenueSeries[idx] || 0;
                    const aip = aiSeries[idx] || 0;
                    body.innerHTML = `
                      <div class="font-semibold mb-1">${dateLabel}</div>
                      <div class="flex items-center justify-between gap-3"><span class="text-slate-200">Выручка</span><span class="font-bold tabular-nums">${adminFormat.moneyAmount(rev)} ₸</span></div>
                      <div class="flex items-center justify-between gap-3"><span class="text-slate-200">Из них ботом</span><span class="font-bold tabular-nums text-indigo-200">${adminFormat.moneyAmount(aip)} ₸</span></div>
                    `;

                    const { chartArea } = chart;
                    const x = tooltip.caretX;
                    const y = tooltip.caretY;
                    el.style.opacity = '1';
                    el.style.left = Math.min(chartArea.right - 220, Math.max(chartArea.left + 8, x + 12)) + 'px';
                    el.style.top = Math.min(chartArea.bottom - 60, Math.max(chartArea.top + 8, y - 18)) + 'px';

                    const ctx2 = chart.ctx;
                    ctx2.save();
                    ctx2.beginPath();
                    ctx2.moveTo(x, chartArea.top);
                    ctx2.lineTo(x, chartArea.bottom);
                    ctx2.strokeStyle = 'rgba(99, 102, 241, 0.25)';
                    ctx2.lineWidth = 1;
                    ctx2.stroke();
                    ctx2.restore();
                },
            };

            try {
                charts.analytics = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels,
                        datasets: [
                            {
                                label: 'Выручка',
                                data: revenueSeries,
                                borderColor: '#6366F1',
                                borderWidth: 2,
                                fill: true,
                                backgroundColor: (context) => {
                                    const chart = context.chart;
                                    const { ctx: cctx, chartArea } = chart;
                                    if (!chartArea) return 'rgba(99, 102, 241, 0.10)';
                                    const g = cctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                                    g.addColorStop(0, 'rgba(99, 102, 241, 0.22)');
                                    g.addColorStop(1, 'rgba(99, 102, 241, 0)');
                                    return g;
                                },
                                tension: 0.4,
                                pointRadius: 0,
                                pointHoverRadius: 4,
                                pointBackgroundColor: '#6366F1',
                            },
                            {
                                label: 'AI Profit',
                                data: aiSeries,
                                borderColor: 'rgba(124, 58, 237, 0.9)',
                                borderWidth: 2,
                                fill: false,
                                tension: 0.4,
                                pointRadius: 0,
                                pointHoverRadius: 4,
                                pointBackgroundColor: 'rgba(124, 58, 237, 0.9)',
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { intersect: false, mode: 'index' },
                        plugins: {
                            legend: { display: false },
                            tooltip: { enabled: false },
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: {
                                    callback: (v) => adminFormat.moneyAmount(v) + ' ₸',
                                    font: chartFont,
                                    color: '#64748b',
                                },
                                grid: { color: 'rgba(15, 23, 42, 0.06)', borderDash: [4, 4] },
                            },
                            x: {
                                ticks: { font: chartFont, color: '#64748b' },
                                grid: { display: false },
                            },
                        },
                    },
                    plugins: [externalTooltipPlugin],
                });
            } catch (e) {
                adminLogger.error('Chart.js (аналитика):', e);
            }

            // KPI sparklines render (tiny, no axes).
            self._renderAnalyticsSparklines(daily);
        },

        _destroyAnalyticsSparklines() {
            const obj = charts.analyticsSparks || {};
            for (const k of Object.keys(obj)) {
                const ch = obj[k];
                if (!ch) continue;
                try { ch.destroy(); } catch (_e) { /* ignore */ }
            }
            charts.analyticsSparks = {};
        },

        _renderAnalyticsSparklines(daily) {
            this._destroyAnalyticsSparklines();
            const defs = [
                { id: 'sparkRevenue', key: 'revenue', color: '#6366F1' },
                { id: 'sparkOrders', key: 'orders', color: '#0ea5e9' },
                { id: 'sparkAvg', key: 'avg_check', color: '#10b981' },
                { id: 'sparkAi', key: 'ai_profit', color: '#7c3aed' },
            ];
            for (const d of defs) {
                const canvas = document.getElementById(d.id);
                if (!canvas) continue;
                const ctx = canvas.getContext('2d');
                if (!ctx) continue;
                let series = [];
                if (d.key === 'avg_check') {
                    series = daily.map((row) => {
                        const rev = Number(row.revenue) || 0;
                        const ord = Number(row.orders) || 0;
                        return ord ? rev / ord : 0;
                    });
                } else {
                    series = daily.map((row) => Number(row[d.key]) || 0);
                }
                try {
                    charts.analyticsSparks[d.id] = new Chart(ctx, {
                        type: 'line',
                        data: { labels: daily.map((row) => row.date), datasets: [{
                            data: series,
                            borderColor: d.color,
                            borderWidth: 2,
                            tension: 0.45,
                            pointRadius: 0,
                            fill: false,
                        }]},
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false }, tooltip: { enabled: false } },
                            scales: { x: { display: false }, y: { display: false } },
                            elements: { line: { capBezierPoints: true } },
                        },
                    });
                } catch (_e) { /* ignore */ }
            }
        },

        /** Плавная area-линия с градиентом под кривой (премиум-дашборд). */
        async renderDashboardMiniChart() {
            if (this.currentTab !== 'dashboard' || this.dashboardTab !== 'overview') return;
            const canvas = document.getElementById('dashboardHeroChart');
            if (!canvas) return;
            const series = this.dashStats.daily_series || [];
            // Пустая серия: не вызываем destroy() — иначе график исчезает навсегда до следующего успешного /stats.
            if (series.length === 0) return;
            await adminEnsureChartJs();

            const ctx = canvas.getContext('2d');
            adminDestroyDashboardChart();
            const chartFont = adminChartJsCommonFont();

            const labels = series.map((d) => {
                const dt = new Date(d.date + 'T12:00:00Z');
                return dt.toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' });
            });
            const metric = this.dashMiniMetric === 'orders' ? 'orders'
                : this.dashMiniMetric === 'ai_profit' ? 'ai_profit'
                    : 'revenue';
            const data = series.map((d) => {
                if (metric === 'revenue') return Number(d.revenue) || 0;
                if (metric === 'ai_profit') return Number(d.ai_profit ?? 0);
                return Number(d.orders) || 0;
            });
            const isMoney = metric === 'revenue' || metric === 'ai_profit';
            const label = metric === 'revenue' ? 'Выручка (₸)'
                : metric === 'ai_profit' ? 'Прибыль ИИ (₸)'
                    : 'Заказов';

            const palette = metric === 'revenue'
                ? {
                    border: '#2563eb',
                    point: '#2563eb',
                    fillFallback: 'rgba(37, 99, 235, 0.08)',
                    g0: 'rgba(37, 99, 235, 0.32)',
                    g1: 'rgba(37, 99, 235, 0.02)',
                }
                : metric === 'ai_profit'
                    ? {
                        border: '#7c3aed',
                        point: '#a855f7',
                        fillFallback: 'rgba(124, 58, 237, 0.08)',
                        g0: 'rgba(168, 85, 247, 0.35)',
                        g1: 'rgba(124, 58, 237, 0.02)',
                    }
                    : {
                        border: '#0f766e',
                        point: '#14b8a6',
                        fillFallback: 'rgba(15, 118, 110, 0.08)',
                        g0: 'rgba(20, 184, 166, 0.28)',
                        g1: 'rgba(15, 118, 110, 0.02)',
                    };

            const self = this;
            /** Заливка через scriptable option: chartArea на первом кадре может быть пустым (документация Chart.js). */
            try {
            charts.dashboard = new Chart(ctx, {
                type: 'line',
                data: {
                    labels,
                    datasets: [{
                        label,
                        data,
                        borderColor: palette.border,
                        borderWidth: 2,
                        fill: true,
                        backgroundColor: (context) => {
                            const chart = context.chart;
                            const { ctx: cctx, chartArea } = chart;
                            if (!chartArea) {
                                return palette.fillFallback;
                            }
                            const g = cctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                            g.addColorStop(0, palette.g0);
                            g.addColorStop(1, palette.g1);
                            return g;
                        },
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        pointBackgroundColor: palette.point,
                        pointBorderColor: '#ffffff',
                        pointBorderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: '#1e293b',
                            padding: 10,
                            cornerRadius: 10,
                            callbacks: {
                                label(c) {
                                    const raw = c.raw;
                                    if (self.dashMiniMetric === 'revenue' || self.dashMiniMetric === 'ai_profit') {
                                        const prefix = self.dashMiniMetric === 'ai_profit' ? 'Прибыль ИИ' : 'Выручка';
                                        return prefix + ': ' + adminFormat.moneyAmount(raw) + ' ₸';
                                    }
                                    return 'Заказов: ' + raw;
                                },
                            },
                        },
                    },
                    scales: {
                        x: {
                            grid: { display: false },
                            border: { display: false },
                            ticks: { font: chartFont, maxRotation: 0 },
                        },
                        y: {
                            beginAtZero: true,
                            grid: { color: 'rgba(226, 232, 240, 0.9)' },
                            border: { display: false },
                            ticks: {
                                font: chartFont,
                                callback: (v) => (isMoney
                                    ? adminFormat.moneyAmount(v)
                                    : v),
                            },
                        },
                    },
                },
            });
            } catch (e) {
                adminLogger.error('Chart.js (дашборд):', e);
            }
        },

        async sendTestMessage() {
            const text = this.testInput.trim();
            if (!text || this.testLoading) return;
            this.testMessages.push({ role: 'user', text });
            this.testInput = '';
            this.testLoading = true;

            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/test-bot', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text }),
                });
                if (ok) {
                    this.testMessages.push({
                        role: 'assistant',
                        text: data.reply,
                        intent: data.intent || null,
                    });
                } else {
                    this.testMessages.push({ role: 'assistant', text: 'Ошибка: сервер вернул ' + status });
                }
            } catch (_e) {
                this.testMessages.push({ role: 'assistant', text: 'Ошибка: не удалось получить ответ.' });
            }
            this.testLoading = false;
            this.$nextTick(() => {
                const area = document.getElementById('testChatArea');
                if (area) area.scrollTop = area.scrollHeight;
            });
        },

        // ─── Helpers ─────────────────────────────────
        formatTrendPct(pct) {
            const n = Number(pct);
            if (!Number.isFinite(n)) return '';
            if (n === 0) return '0%';
            const sign = n > 0 ? '+' : '';
            return sign + n + '%';
        },
    };
}

/**
 * Слияние миксинов через дескрипторы свойств.
 * Object.assign вызывает [[Get]] у геттеров при копировании — тогда this указывает на фрагмент миксина без menuItems/orders и Alpine падает при инициализации.
 */
function mergeAdminMixins(...sources) {
    const target = {};
    for (const src of sources) {
        for (const key of Reflect.ownKeys(src)) {
            const desc = Object.getOwnPropertyDescriptor(src, key);
            if (desc) Object.defineProperty(target, key, desc);
        }
    }
    return target;
}

/** Focus-Driven OS Sprint 3 — Action Queue inbox cards + Final Mile voice strip. */
function adminMixinInboxActionQueue() {
    return {
        moneyQueueStatusClass(severity) {
            const s = String(severity || 'info');
            if (s === 'critical') return 'ds-status-danger';
            if (s === 'warning') return 'ds-status-warn';
            return 'ds-status-inactive';
        },

        moneyQueueStatusDotClass(severity) {
            const s = String(severity || 'info');
            if (s === 'critical') return 'ds-status-danger';
            if (s === 'warning') return 'ds-status-warn';
            return 'ds-status-inactive';
        },

        moneyQueueKindLabel(kind) {
            const k = String(kind || '');
            if (k === 'abandoned_draft') return 'Брошенный заказ';
            if (k === 'pending_prepay') return 'Ожидает оплату';
            if (k === 'slow_chat') return 'Ждёт ответ';
            if (k === 'high_value_stuck') return 'Крупный заказ';
            if (k === 'menu_confusion') return 'Путаница в меню';
            if (k === 'booking_at_risk') return 'Бронь под риском';
            return 'Внимание';
        },

        async loadMoneyQueue() {
            this.moneyQueueLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse(
                    `/api/admin/inbox/money-queue${this.locationQueryString('?')}`,
                );
                if (!ok) {
                    adminLogger.warn('[admin] loadMoneyQueue', status, data);
                    return;
                }
                this.moneyQueue = data;
            } finally {
                this.moneyQueueLoading = false;
            }
        },

        loadInboxActionQueue() {
            return this.loadMoneyQueue();
        },

        runMoneyQueueAction(action, item) {
            if (!action) return;
            if (this.shouldRouteMoneyQueueViaShift(action)) {
                void this.openMoneyQueueItemViaShift(action, item);
                return;
            }
            const tab = String(action.tab || '').trim();
            if (tab === 'orders' && action.order_id != null) {
                this.openGuestContextOrder({ id: Number(action.order_id) });
                return;
            }
            if (tab === 'chats' && action.phone) {
                void this.openHelpChat(String(action.phone));
                return;
            }
            if (tab === 'inbox') {
                this.navigateToTab('inbox', { inboxTab: action.inboxTab || 'clients' });
                return;
            }
            if (tab) {
                const opts = {};
                if (action.inboxTab) opts.inboxTab = action.inboxTab;
                if (action.chatPulseFilter) opts.chatPulseFilter = action.chatPulseFilter;
                if (action.orderSumMin != null) opts.orderSumMin = action.orderSumMin;
                this.navigateToTab(tab, opts);
            }
        },

        shouldRouteMoneyQueueViaShift(action) {
            if (this.effectiveStaffRole() !== 'operator') return false;
            const tab = String(action?.tab || '').trim();
            return tab === 'chats' || tab === 'orders';
        },

        async openMoneyQueueItemViaShift(action, item) {
            if (!action) return;
            this.navigateToTab('shift');
            try {
                await this.loadShiftState(true);
            } catch (_e) {
                /* loadShiftState handles toast */
            }
            this.mobileActiveScreen = 'focus';
            const f = this.shiftState?.focus;
            const phoneMatch =
                action.tab === 'chats'
                && action.phone
                && f
                && String(f.phone || '') === String(action.phone);
            const orderMatch =
                action.tab === 'orders'
                && action.order_id != null
                && f
                && Number(f.order_id) === Number(action.order_id);
            if ((phoneMatch || orderMatch) && this.shiftHasContextDock()) {
                this.openShiftContext();
                return;
            }
            if (action.tab === 'chats' && action.phone) {
                void this.openHelpChat(String(action.phone));
                return;
            }
            if (action.tab === 'orders' && action.order_id != null) {
                this.openGuestContextOrder({ id: Number(action.order_id) });
                return;
            }
            const primary = (item?.actions || action.actions || [])[0];
            if (primary) this.runShiftFocusAction(primary);
        },

        inboxShowsShiftHero() {
            return this.effectiveStaffRole() === 'operator'
                && !!this.focusCardView()
                && this.inboxTab === 'clients';
        },

        openInboxShiftHero() {
            this.navigateToTab('shift');
            void this.loadShiftState(true).then(() => {
                if (this.shiftHasContextDock()) this.openShiftContext();
            });
        },

        refreshVoiceCallStrip() {
            this.voiceCallLogsOffset = 0;
            return this.loadVoiceCallLogs({ append: false });
        },

        loadMoreVoiceCallLogs() {
            return this.loadVoiceCallLogs({ append: true });
        },

        async loadVoiceCallLogs({ append = false } = {}) {
            if (this.voiceCallLogsLoading) return;
            this.voiceCallLogsLoading = true;
            this.voiceCallLogsUnavailable = false;
            const limit = Number(this.voiceCallLogsLimit) || 15;
            const offset = append ? Number(this.voiceCallLogsOffset) || 0 : 0;
            try {
                const q = this.locationQueryParams();
                q.set('limit', String(limit));
                q.set('offset', String(offset));
                const { ok, status, data } = await this.apiJsonResponse(
                    `/api/admin/intelligence/voice/calls?${q.toString()}`,
                );
                if (ok && Array.isArray(data?.items)) {
                    const items = data.items;
                    const total = Number(data.total ?? items.length);
                    this.voiceCallLogs = append ? [...(this.voiceCallLogs || []), ...items] : items;
                    this.voiceCallLogsTotal = total;
                    this.voiceCallLogsOffset = offset + items.length;
                    this.voiceCallLogsHasMore = this.voiceCallLogsOffset < total;
                    return;
                }
                if (status === 404 || status === 501) {
                    if (!append) this.voiceCallLogs = [];
                    this.voiceCallLogsUnavailable = true;
                    return;
                }
                if (!append) this.voiceCallLogs = [];
            } catch (_e) {
                if (!append) this.voiceCallLogs = [];
            } finally {
                this.voiceCallLogsLoading = false;
            }
        },

        voiceCallStatusLabel(status) {
            const raw = String(status || '').toLowerCase();
            const labels = {
                started: 'Начат',
                transcribed: 'Расшифрован',
                completed: 'Завершён',
                escalated_whatsapp: 'Передано оператору в WhatsApp',
                escalated: 'Передано оператору в WhatsApp',
                error: 'Ошибка',
            };
            return labels[raw] || raw || '—';
        },

        voiceCallModeLabel(mode) {
            const raw = String(mode || '').toLowerCase();
            const labels = {
                stt_fallback: 'Распознавание речи',
                realtime: 'Потоковый диалог',
            };
            return labels[raw] || mode || '';
        },

        voiceCallStatusSurfaceClass(status) {
            const raw = String(status || '').toLowerCase();
            if (raw === 'completed') return 'ds-status-ok ds-status-ring';
            if (raw === 'error') return 'ds-status-danger ds-status-ring';
            if (raw.includes('escalat')) return 'ds-status-warn ds-status-ring';
            return 'ds-status-inactive ds-status-ring';
        },

        voiceCallDurationLabel(log) {
            const payload = (log && typeof log.payload === 'object') ? log.payload : {};
            const sec = Number(log?.duration_sec ?? payload?.duration_sec ?? payload?.duration ?? 0);
            if (!Number.isFinite(sec) || sec <= 0) return '—';
            if (sec < 60) return `${Math.round(sec)} с`;
            const m = Math.floor(sec / 60);
            const s = Math.round(sec % 60);
            return `${m} мин ${s} с`;
        },

        voiceCallRecordingUrl(log) {
            const payload = (log && typeof log.payload === 'object') ? log.payload : {};
            const url = String(log?.recording_url || payload?.recording_url || payload?.recording || '').trim();
            return url || '';
        },
    };
}

function adminApp() {
    return mergeAdminMixins(
        adminMixinState(),
        adminMixinModeEngine(),
        adminMixinShiftStagedNav(),
        adminMixinCommandBar(),
        adminMixinMenuOrdersUi(),
        adminMixinSearchBookings(),
        adminMixinAuthKnowledge(),
        adminMixinPackagingIntegrationsDemoWsUi(),
        adminMixinWebSocketEvents(),
        adminMixinLiveChat(),
        adminMixinInboxActionQueue(),
        adminMixinDataChartsSettings(),
    );
}

window.adminApp = adminApp;

function marketingTab() {
    return {
        subTab: 'blasts',
        blasts: [],
        loading: false,
        saving: false,
        formError: '',
        segmentCount: null,
        form: { name: '', segment_type: 'inactive_30d', message_text: '', template_name: '', scheduled_for: '' },
        iikoSyncLoading: false,
        iikoSyncResult: null,
        iikoSyncError: '',
        loyaltyEnabled: false,
        loyaltyPointsPerKzt: 0,
        loyaltyHistory: [],
        loyaltyBalance: 0,
        adjustPhone: '',
        adjustPoints: 0,
        adjustNote: '',
        adjustResult: '',
        confirm: { open: false, title: '', body: '', danger: false, _resolve: null },

        openConfirm(title, body, danger = false) {
            return new Promise(resolve => {
                this.confirm = { open: true, title, body, danger, _resolve: resolve };
            });
        },
        doConfirm(ok) {
            const resolve = this.confirm._resolve;
            this.confirm.open = false;
            if (resolve) resolve(ok);
        },

        async init() {
            await this.loadBlasts();
        },

        async loadBlasts() {
            this.loading = true;
            try {
                const r = await fetch('/api/admin/marketing/blasts');
                if (r.ok) { const d = await r.json(); this.blasts = d.items || []; }
            } catch(_e) {}
            this.loading = false;
        },

        async previewSegment() {
            this.segmentCount = null;
            try {
                const r = await fetch(`/api/admin/marketing/segment-preview/${this.form.segment_type}`);
                if (r.ok) { const d = await r.json(); this.segmentCount = d.count; }
            } catch(_e) {}
        },

        async syncIikoCustomers() {
            this.iikoSyncLoading = true;
            this.iikoSyncError = '';
            this.iikoSyncResult = null;
            try {
                const r = await fetch('/api/admin/marketing/sync-iiko-customers?days=90', { method: 'POST' });
                const d = await r.json().catch(() => ({}));
                if (!r.ok) {
                    this.iikoSyncError = (typeof d.detail === 'string' ? d.detail : d.detail?.msg) || 'Не удалось импортировать базу';
                    return;
                }
                this.iikoSyncResult = d;
                await this.previewSegment();
            } catch (_e) {
                this.iikoSyncError = 'Сетевая ошибка';
            } finally {
                this.iikoSyncLoading = false;
            }
        },

        async createBlast() {
            this.formError = '';
            if (!this.form.name.trim() || !this.form.message_text.trim()) {
                this.formError = 'Заполните название и текст'; return;
            }
            this.saving = true;
            try {
                const payload = { ...this.form };
                if (!payload.scheduled_for) payload.scheduled_for = null;
                if (!payload.template_name) payload.template_name = null;

                const r = await fetch('/api/admin/marketing/blasts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (r.ok) {
                    this.form = { name: '', segment_type: 'inactive_30d', message_text: '', template_name: '', scheduled_for: '' };
                    this.segmentCount = null;
                    await this.loadBlasts();
                } else {
                    const d = await r.json();
                    this.formError = d.detail || 'Ошибка создания';
                }
            } catch(_e) { this.formError = 'Сетевая ошибка'; }
            this.saving = false;
        },

        async sendBlast(id) {
            const ok = await this.openConfirm('Запустить рассылку?', 'Сообщения уйдут получателям. При необходимости можно отменить кнопкой «Отменить».', true);
            if (!ok) return;
            try {
                await fetch(`/api/admin/marketing/blasts/${id}/send`, { method: 'POST' });
                await this.loadBlasts();
            } catch(_e) {}
        },

        async cancelBlast(id) {
            const ok = await this.openConfirm('Отменить рассылку?', 'Отправка будет остановлена. Уже отправленные сообщения не отзываются.');
            if (!ok) return;
            try {
                await fetch(`/api/admin/marketing/blasts/${id}/cancel`, { method: 'POST' });
                await this.loadBlasts();
            } catch(_e) {}
        },

        duplicateBlast(blast) {
            this.form = {
                name: blast.name + ' (копия)',
                segment_type: blast.segment_type,
                message_text: blast.message_text,
                template_name: blast.template_name || '',
                scheduled_for: '',
            };
            this.previewSegment();
            document.getElementById('rm-tab-marketing')?.scrollIntoView({ behavior: 'smooth' });
        },

        async deleteBlast(id) {
            const ok = await this.openConfirm('Удалить рассылку?', 'Рассылка и список получателей будут удалены безвозвратно.');
            if (!ok) return;
            try {
                await fetch(`/api/admin/marketing/blasts/${id}`, { method: 'DELETE' });
                await this.loadBlasts();
            } catch(_e) {}
        },

        async loadLoyalty() {
            try {
                const r = await fetch('/api/admin/settings/environment');
                if (r.ok) {
                    const d = await r.json();
                    const lo = d.loyalty && typeof d.loyalty === 'object' ? d.loyalty : {};
                    this.loyaltyEnabled = !!lo.enabled;
                    this.loyaltyPointsPerKzt = Number(lo.points_per_kzt) || 0;
                }
            } catch (_e) {}
        },

        async loadLoyaltyHistory() {
            if (!this.adjustPhone) return;
            this.loyaltyHistory = [];
            this.loyaltyBalance = 0;
            try {
                const r = await fetch(`/api/admin/loyalty/transactions?phone=${encodeURIComponent(this.adjustPhone)}`);
                if (r.ok) {
                    const d = await r.json();
                    this.loyaltyHistory = d.transactions || [];
                    this.loyaltyBalance = d.balance || 0;
                }
            } catch(_e) {}
        },

        async submitAdjust() {
            if (!this.adjustPhone || this.adjustPoints === 0) return;
            this.adjustResult = '';
            try {
                const r = await fetch('/api/admin/loyalty/adjust', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ phone: this.adjustPhone, points: this.adjustPoints, note: this.adjustNote }),
                });
                if (r.ok) {
                    const d = await r.json();
                    this.adjustResult = `✅ Новый баланс: ${d.new_balance} баллов`;
                    this.adjustPoints = 0;
                    await this.loadLoyaltyHistory();
                } else {
                    const d = await r.json();
                    this.adjustResult = `❌ ${d.detail || 'Ошибка'}`;
                }
            } catch(_e) { this.adjustResult = '❌ Сетевая ошибка'; }
        },
    };
}
window.marketingTab = marketingTab;
