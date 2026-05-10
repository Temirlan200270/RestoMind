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
        else if (subTab === 'value') ac = 'value';
        return { ...empty, tab: 'ai_center', phone, aiCenterTab: ac };
    }

    return { ...empty, tab: path || null, phone };
}

/** Допустимые верхнеуровневые вкладки (id из navItems). */
const ADMIN_TOP_TAB_IDS = new Set([
    'dashboard', 'inbox', 'ai_center', 'orders', 'bookings', 'chats', 'menu', 'settings',
]);

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
        teamTempPassword: '',

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
        /** Под-табы ИИ-центра: вклад / инсайты / нагрузка (P1.5.0). */
        aiCenterTab: 'value',
        /** Вкладка внутри Settings (Stripe-like). */
        settingsTab: 'restaurant', // restaurant | branding | connections | smart_sales | team | …
        orgProfileDirty: false,
        brandingDirty: false,
        navItems: [
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
            schedule_json: null,
            schedule_json_text: '',
            operational_label: '',
            is_business_open: false,
            is_kitchen_open: false,
        },
        scheduleEditorOpen: false,
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
            tenant: null,
            branding: null,
        },
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
        menuItems: [],

        // Заказы
        ordersView: 'kanban',
        kanbanDensity: 'normal',
        /** Подсказка режима заказов (канбан / таблица), скрывается через localStorage */
        ordersKanbanHintDismissed:
            typeof localStorage !== 'undefined' &&
            localStorage.getItem('rm_orders_view_hint_v1') === '1',
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
        intelligenceLoading: false,
        intelligenceAsking: false,
        intelligenceQuestion: '',
        intelligenceAnswer: '',
        intelligenceConversationId: null,
        intelligenceData: { summary: null, insights: [], snapshot: null },
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
        /** Время последнего успешного GET /incidents?mode=summary (кэш ~45 с на дашборде) */
        attentionSummaryFetchedAt: 0,
        /** Скрыть системный баннер до следующей загрузки страницы */
        systemBannerDismissed: false,
        orderTimeline: [],
        orderTimelineLoading: false,
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
        chatTriageMode: 'active',
        chatPhone: '',
        activeChatPhone: '',
        chatListLoading: false,
        chatListHasMore: true,
        chatListCursorAt: null,
        chatListCursorId: null,
        /** На планшетах/мобилке: выезжающая панель «О клиенте» (на lg — колонка справа) */
        chatMobileInfoOpen: false,
        activeChatState: 'chatting',
        chatMessages: [],
        chatMessagesHasMore: false,
        chatMessagesBeforeId: null,
        chatMessagesLoadingOlder: false,
        operatorInput: '',
        unreadChats: 0,
        kanbanVisible: { draft: 20, confirmed: 20, sent_to_iiko: 20, in_transit: 20, waiting_pickup: 20, completed: 20 },
        upsellFeedbackLoading: false,
        /** Кабина оператора: сводка по клиенту */
        customerSummaryLoading: false,
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
        },
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
        setupStatus: { score: 0, steps: [], menu_items: 0, upsell_rules: 0, packaging_rules: 0, knowledge_items: 0, tokens_today: null },
        iikoOnboardApiLogin: '',
        iikoOnboardOrgs: [],
        iikoOnboardSelectedOrg: '',
        iikoOnboardTerminal: '',
        iikoOnboardVerifyLoading: false,
        iikoOnboardSetupLoading: false,
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

        get menuCategoriesKitchenList() {
            this._refreshMenuView();
            return this._menuKitchenListCache;
        },

        get menuCategoriesBarList() {
            this._refreshMenuView();
            return this._menuBarListCache;
        },

        get filteredChatList() {
            if (!this.chatSearch.trim()) return this.chatList;
            const q = this.chatSearch.trim().toLowerCase();
            return this.chatList.filter(c => c.phone.toLowerCase().includes(q));
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
                this.menuCategoryChipsOpen = m;
                this.menuToolbarExpanded = m;
                // Mobile-first: заказы без горизонтального скролла (канбан — для больших экранов).
                this.ordersView = m ? (this.ordersView || 'kanban') : 'table';
                const savedKanbanDensity = localStorage.getItem('rm_kanban_density_v1');
                if (savedKanbanDensity === 'compact' || savedKanbanDensity === 'normal') {
                    this.kanbanDensity = savedKanbanDensity;
                }
            } catch (_e) {
                this.menuCategoryChipsOpen = true;
                this.menuToolbarExpanded = true;
            }

            await this.checkSession();

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

        /** Бейдж пункта «Требует внимания»: открытые задачи помощи + открытые инциденты. */
        inboxTotalOpen() {
            return Number(this.dashStats?.failed_tasks_open || 0) + this.incidentsTotalOpen();
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

        kanbanOrderSurfaceClass(order, normalClass) {
            if (order?.iiko_last_error) {
                return 'bg-gradient-to-br from-rose-50 via-white to-red-50/40 border-2 border-red-400 border-l-[5px] border-l-red-600 shadow-md ring-2 ring-red-200/90 ring-offset-1 ring-offset-white';
            }
            let c = normalClass;
            if (this.orderAwaitingOrderPrepay(order)) {
                c += ' ring-2 ring-amber-400 ring-offset-2 ring-offset-white border-amber-400';
            }
            return c;
        },

        setKanbanDensity(mode) {
            const next = mode === 'compact' ? 'compact' : 'normal';
            this.kanbanDensity = next;
            try {
                localStorage.setItem('rm_kanban_density_v1', next);
            } catch (_e) {}
        },

        kanbanCompactEnabled() {
            try {
                return this.ordersView === 'kanban' &&
                    this.kanbanDensity === 'compact' &&
                    window.matchMedia('(min-width: 768px)').matches;
            } catch (_e) {
                return this.ordersView === 'kanban' && this.kanbanDensity === 'compact';
            }
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
            this.showOrderModal = true;
            void this.loadOrderTimeline(id);
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

        /** Подтверждение + удаление заказа(ов). Одна модалка с чекбоксом — меньше «confirmation fatigue», но всё ещё требует осознанного клика. */
        async confirmAndDeleteOrders(ids, source) {
            const clean = [...new Set((ids || []).map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0))].sort((a, b) => a - b);
            if (!clean.length) return;
            const src = source || '';
            let message;
            let confirmText;
            let title;
            if (clean.length === 1) {
                title = 'Удалить заказ?';
                const id = clean[0];
                const o = this.selectedOrder && Number(this.selectedOrder.id) === id ? this.selectedOrder : null;
                const lines = [`Заказ #${id} будет удалён из базы безвозвратно — пропадёт из списков, карточек гостя и аналитики.`];
                if (o) {
                    const bits = [];
                    if (o.total_price) bits.push(this.fmt.money(o.total_price));
                    if (o.user_phone) bits.push(o.user_phone);
                    if (o.created_at) bits.push(this.fmt.date(o.created_at));
                    if (bits.length) lines.push(bits.join(' · '));
                }
                message = lines.join('\n\n');
                confirmText = 'Удалить заказ';
            } else {
                title = 'Удалить заказы?';
                message = `Безвозвратно удалятся ${clean.length} заказов: ${clean.map((i) => '#' + i).join(', ')}. Данные уйдут из списков и аналитики.`;
                confirmText = `Удалить заказы (${clean.length})`;
            }
            const r = await this.openUiConfirm({
                title,
                message,
                danger: true,
                confirmText,
                cancelText: 'Отмена',
                requireAck: true,
                ackLabel: clean.length === 1
                    ? 'Я понимаю, что заказ нельзя будет восстановить'
                    : 'Я понимаю, что эти заказы нельзя будет восстановить',
            });
            if (!r.ok) return;
            await this._executeOrderDeleteDirect(clean, src);
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
            if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
                e.preventDefault();
                this.openGlobalSearch();
                return;
            }
            if (e.key !== 'Escape') return;
            /** Закрытие оверлеев сверху вниз (одна Esc — один слой). */
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

        get filteredBookings() {
            if (this.bookingStatusFilter === 'all') return this.bookings;
            return this.bookings.filter(b => b.status === this.bookingStatusFilter);
        },

        bookingStatsFor(status) {
            return this.bookings.filter(b => b.status === status).length;
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
            const res = await this.apiFetch(url, fetchOpts);
            const data = await res.json().catch(() => ({}));
            // Диагностика "вкладка пустая": если API вернул не-2xx, логируем URL+статус.
            // Уровень warn виден по умолчанию.
            if (!res.ok) {
                try {
                    adminLogger.warn('[api]', String(url), res.status, data);
                } catch (_e) { /* ignore */ }
            }
            return { ok: res.ok, status: res.status, data, res };
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
                tenant: root.tenant || null,
                branding: root.branding || null,
                ws_token: root.ws_token || '',
            };
        },

        async selectOrganization(orgId) {
            if (!orgId || (this.orgProfile && orgId === this.orgProfile.id)) return;
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
                this.syncBrandingDraftFromUser();
                this.wsToken = me.ws_token;
                this.staffRole = me.role;
                this.isSuperadmin = me.is_superadmin;
                
                await this.loadOrgProfile();
                this.connectWebSocket();
                await this.loadTabData();
                await this.loadIntegrationStatus();
                await this.loadChatList();
                this.setToast('Филиал переключен');
            } catch (e) {
                adminLogger.error('[admin] selectOrganization', e);
            } finally {
                this.aiValueLoading = false;
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
                        this.currentTab = this.staffRole === 'operator' ? 'chats' : 'dashboard';
                        this._pushAdminHash();
                    } else {
                        this._applyParsedHash(parsed);
                    }
                    this._installAdminHashWatch();
                    await this.refreshDemoStatus();
                    await this.loadOrgProfile();
                    this.connectWebSocket();
                    await this.loadTabData();
                    await this.loadIntegrationStatus();
                    await this.loadChatList();
                    await this._consumePendingHashChatPhone();
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
                    this.currentTab = this.staffRole === 'operator' ? 'chats' : 'dashboard';
                    this._pushAdminHash();
                } else {
                    this._applyParsedHash(parsedLogin);
                }
                this._installAdminHashWatch();
                await this.refreshDemoStatus();
                await this.loadOrgProfile();
                this.connectWebSocket();
                await this.loadTabData();
                await this.loadIntegrationStatus();
                await this.loadChatList();
                await this._consumePendingHashChatPhone();
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
                    this.currentTab = 'dashboard';
                    this._pushAdminHash();
                } else {
                    this._applyParsedHash(parsedLogin);
                }
                this._installAdminHashWatch();
                await this.refreshDemoStatus();
                await this.loadOrgProfile();
                this.connectWebSocket();
                await this.loadTabData();
                await this.loadIntegrationStatus();
                await this.loadChatList();
                await this._consumePendingHashChatPhone();
            } catch {
                this.loginError = 'Не удалось связаться с сервером';
            } finally {
                this.loginLoading = false;
            }
        },

        async loadOrgProfile() {
            this.orgProfileLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/organization/profile');
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
                this.orgProfile = {
                    id: data?.id ?? this.orgProfile?.id ?? null,
                    organization_id: data?.organization_id ?? this.orgProfile?.organization_id ?? null,
                    name: (data?.name || nm).trim(),
                    timezone: (data?.timezone || '').trim(),
                    currency: (data?.currency || '').trim(),
                    whatsapp_phone_number_id: (data?.whatsapp_phone_number_id || '').trim(),
                    telegram_ops_chat_id: (data?.telegram_ops_chat_id || '').trim(),
                    prepayment_legal_text: String(data?.prepayment_legal_text ?? '').trim(),
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
                void this.showUiAlert(url, 'Webhook URL');
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
                        tokens_today: r.data.tokens_today != null ? Number(r.data.tokens_today) : null,
                    };
                    const sc = Number(this.setupStatus.score ?? 0);
                    if (sc >= 60) this.setupProgressExpanded = false;
                    else if (sc <= 30) this.setupProgressExpanded = true;
                    if (sc >= 100 && sessionStorage.getItem('restomind_setup_done_toast') !== '1') {
                        sessionStorage.setItem('restomind_setup_done_toast', '1');
                        void this.showUiAlert('Готово: филиал полностью настроен.', 'Готово');
                    }
                }
            } catch { /* ignore */ }
        },

        async loadIntegrationStatus() {
            try {
                const st = await this.apiJsonResponse('/api/admin/integrations/status');
                if (st.ok) this.mergeIntegrationStatus(st.data);
                const ev = await this.apiJsonResponse('/api/admin/integrations/events?limit=40');
                if (ev.ok) this.integrationEvents = ev.data.events || [];
                await this.loadSetupStatus();
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

            this.ws.onclose = () => {
                this._clearWsReadyTimer();
                this.wsChannelReady = false;
                this.ws = null;
                this.wsEpoch++;
                // Важно для диагностики: если сервер закрывает до accept(), браузер пишет
                // "WebSocket is closed before the connection is established" без деталей.
                // CloseEvent.code/reason помогают отличить Unauthorized (4003) от сетевых проблем.
                try {
                    const ev = arguments && arguments.length ? arguments[0] : null;
                    const code = ev && typeof ev.code === 'number' ? ev.code : null;
                    const reason = ev && typeof ev.reason === 'string' ? ev.reason : '';
                    if (code != null) {
                        adminLogger.warn(`[WS] Disconnected code=${code}${reason ? ` reason="${reason}"` : ''}`);
                    } else {
                        adminLogger.debug('[WS] Disconnected');
                    }
                } catch (_e) {
                    adminLogger.debug('[WS] Disconnected');
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
                this.wsEpoch++;
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
            }
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

        onNewMessage(data) {
            const chatIdx = this.chatList.findIndex(c => c.phone === data.phone);
            if (chatIdx >= 0) {
                this.chatList[chatIdx].lastMessage = data.content?.slice(0, 60) || '';
                this.chatList[chatIdx].lastAt = new Date().toISOString();
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
                    lastAt: new Date().toISOString(),
                });
                this.unreadChats = this.chatList.filter(c => c.unread).length;
            }

            if (data.phone === this.activeChatPhone) {
                this.chatMessages.push({
                    id: data.id,
                    role: data.role,
                    content: data.content,
                    created_at: data.created_at || new Date().toISOString(),
                    delivery_status: data.delivery_status ?? null,
                    provider_message_id: data.provider_message_id ?? null,
                    error_details: data.error_details ?? null,
                    status_updated_at: data.status_updated_at ?? null,
                });
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
                this.$watch('currentTab', () => this._schedulePushAdminHash());
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

        /**
         * Навигация по верхнему меню (и из кода): синхронизирует hash и грузит данные.
         * @param {string} tabId
         * @param {{ settingsTab?: string }} [opts]
         */
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

        navigateToTab(tabId, opts) {
            const o = opts && typeof opts === 'object' ? opts : {};
            this.currentTab = tabId;
            this.ordersMobileFiltersOpen = false;
            if (tabId !== 'chats') {
                this.chatMobileInfoOpen = false;
            }
            if (tabId === 'settings' && typeof o.settingsTab === 'string' && o.settingsTab.trim()) {
                const st = o.settingsTab.trim();
                if (this._adminSettingsTabIds.has(st)) this.settingsTab = st;
            }
            if (tabId === 'menu' && (o.menuView === 'stoplist' || o.menuView === 'catalog')) {
                this.menuView = o.menuView;
            }
            if (tabId === 'inbox') {
                if (typeof o.inboxTab === 'string' && o.inboxTab.trim()) {
                    this.inboxTab = o.inboxTab.trim() === 'system' ? 'system' : 'clients';
                } else {
                    this.inboxTab = 'clients';
                }
            }
            if (tabId === 'dashboard') {
                if (typeof o.dashboardTab === 'string' && o.dashboardTab.trim()) {
                    this.dashboardTab = o.dashboardTab.trim() === 'analytics' ? 'analytics' : 'overview';
                } else {
                    this.dashboardTab = 'overview';
                }
            }
            if (tabId === 'ai_center') {
                if (typeof o.aiCenterTab === 'string' && o.aiCenterTab.trim()) {
                    const a = o.aiCenterTab.trim();
                    if (a === 'insights') this.aiCenterTab = 'insights';
                    else if (a === 'load') this.aiCenterTab = 'load';
                    else this.aiCenterTab = 'value';
                } else {
                    this.aiCenterTab = 'value';
                }
            }
            if (tabId !== 'inbox') this.inboxTab = 'clients';
            if (tabId !== 'dashboard') this.dashboardTab = 'overview';
            if (tabId !== 'ai_center') this.aiCenterTab = 'value';
            this.sidebarOpen = false;
            void this.loadTabData();
            this._schedulePushAdminHash();
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
            } catch (e) {
                adminLogger.warn('loadChatList', e);
            } finally {
                this.chatListLoading = false;
            }
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
                user_exists: false,
                phone,
                name: null,
                total_orders: 0,
                revenue_orders: 0,
                total_spent: 0,
                avg_check: 0,
                is_blocked: false,
                ai_paused: false,
                operator_note: '',
            };

            const chatIdx = this.chatList.findIndex(c => c.phone === phone);
            if (chatIdx >= 0) {
                this.chatList[chatIdx].unread = false;
                this.unreadChats = this.chatList.filter(c => c.unread).length;
            } else {
                this.chatList.unshift({ phone, lastMessage: '', state: 'chatting', unread: false });
            }

            try {
                const stateRes = await this.apiFetch(`/api/admin/chats/${encodeURIComponent(phone)}/state`);
                if (this._selectChatRequestId !== requestId || this.activeChatPhone !== phone) return;
                if (stateRes.ok) {
                    const stateData = await stateRes.json();
                    this.activeChatState = stateData.state;
                    if (chatIdx >= 0) this.chatList[chatIdx].state = stateData.state;
                }
            } catch {}

            try {
                const res = await this.apiFetch(`/api/admin/chats/${encodeURIComponent(phone)}?limit=50`);
                if (this._selectChatRequestId !== requestId || this.activeChatPhone !== phone) return;
                if (res.ok) {
                    const data = await res.json();
                    this.chatMessages = data.messages || [];
                    this.chatMessagesHasMore = !!data.has_more;
                    this.chatMessagesBeforeId = data.next_before_id ?? null;
                    const uix = this.chatList.findIndex(c => c.phone === phone);
                    if (uix >= 0 && data.user_name) {
                        this.chatList[uix].userName = data.user_name;
                    }
                } else {
                    this.chatMessages = [];
                }
            } catch {
                this.chatMessages = [];
            }

            this.scrollChatToBottom();
            await this.loadCustomerSummary(phone);
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
                const res = await this.apiFetch(`/api/admin/chats/${encodeURIComponent(phone)}?limit=50&before_id=${encodeURIComponent(String(beforeId))}`);
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
                return;
            }
            const key = phone.trim();
            const enc = encodeURIComponent(key);
            this.customerSummaryLoading = true;
            try {
                const res = await this.apiFetch(`/api/admin/customers/${enc}/summary`);
                if (this.activeChatPhone?.trim() !== key) return;
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
                        operator_note: data.operator_note ?? '',
                    };
                }
            } catch (e) {
                adminLogger.error('loadCustomerSummary', e);
            } finally {
                if (this.activeChatPhone?.trim() === key) {
                    this.customerSummaryLoading = false;
                }
            }
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
            } catch (e) {
                adminLogger.error('saveCustomerNote', e);
            }
        },

        applyCannedResponse(text) {
            if (!text || this.chatIsBotActive()) return;
            const cur = this.operatorInput || '';
            this.operatorInput = cur.trim() ? `${cur.trim()}\n${text}` : text;
        },

        scrollChatToBottom() {
            this.$nextTick(() => {
                const area = document.getElementById('admin-chat-messages');
                if (area) area.scrollTop = area.scrollHeight;
            });
        },

        /** true — бот/ИИ ведёт диалог, поле ввода заблокировано */
        chatIsBotActive() {
            if (this.customerSummary?.user_exists && this.customerSummary.ai_paused) {
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
            if (!text || !this.activeChatPhone || this.chatIsBotActive()) return;
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
            this.tabDataLoading = true;
            try {
                if (this.currentTab === 'dashboard') {
                    if (this.dashboardTab === 'analytics') {
                        await this.loadAnalytics();
                    } else {
                        await Promise.all([
                            this.loadDashStats(),
                            this.loadAttentionSummary(),
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
                    } else {
                        await this.loadAiValue();
                    }
                } else if (this.currentTab === 'inbox') {
                    if (this.inboxTab === 'system') {
                        await this.loadIncidents();
                    } else {
                        await Promise.all([this.loadFailedTasks(), this.loadDashStats()]);
                    }
                } else if (this.currentTab === 'orders') {
                    await this.loadOrders();
                } else if (this.currentTab === 'bookings') {
                    await this.loadBookings();
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
                        await this.loadIntegrationStatus();
                    } else if (this.settingsTab === 'smart_sales') {
                        await this.loadUpsellRules();
                    } else if (this.settingsTab === 'team') {
                        await this.loadTeam();
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

        async loadDashStats() {
            const initial = !this.dashStatsLoadedOnce;
            if (initial) {
                this.dashStatsLoading = true;
            }
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/stats');
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
                const r = await this.apiJsonResponse(url);
                if (r.ok && r.data) {
                    this.aiValueData = this.normalizeAiValuePayload(r.data, 'ai-value');
                    this.aiValueSource = 'ai-value';
                    return;
                }
                const st = await this.apiJsonResponse('/api/admin/stats');
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
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/overview');
                if (!ok) return;
                this.intelligenceData = {
                    summary: data.summary || null,
                    insights: Array.isArray(data.insights) ? data.insights : [],
                    snapshot: data.snapshot || null,
                };
                if (this.intelligenceData.summary?.current?.avg_check) {
                    this.digitalTwinSim.avg_check = Number(this.intelligenceData.summary.current.avg_check) || this.digitalTwinSim.avg_check;
                }
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
                const { ok, data } = await this.apiJsonResponse('/api/admin/intelligence/digital-twin');
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
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/activity?limit=25');
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
                const { ok, data } = await this.apiJsonResponse('/api/admin/incidents?mode=summary');
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
            this.navigateToTab(tab);
        },

        async loadDashRoiSummary() {
            this.dashRoiLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/roi/today');
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
                const { ok, data } = await this.apiJsonResponse('/api/admin/staff', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        email,
                        role: (this.teamNewRole || 'operator'),
                        password: (this.teamNewPassword || ''),
                    }),
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
            this.bookingsLoadError = '';
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/bookings');
                if (!ok) {
                    this.bookings = [];
                    this.bookingsLoadError = this.formatApiError(data) || 'Не удалось загрузить брони';
                    return;
                }
                this.bookings = data.bookings || [];
            } catch {
                this.bookings = [];
                this.bookingsLoadError = 'Ошибка сети';
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
            this.menuBulkMode = !this.menuBulkMode;
            if (!this.menuBulkMode) {
                this.menuBulkSelectedIds = [];
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

        async menuBulkApplyAvailability(available) {
            const ids = [...this.menuBulkSelectedIds];
            if (!ids.length || this.menuBulkSaving) return;
            this.menuBulkSaving = true;
            try {
                const results = await Promise.all(
                    ids.map(async (id) => {
                        const res = await this.apiFetch(`/api/admin/menu/${id}`, {
                            method: 'PATCH',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ is_available: available }),
                        });
                        return res.ok;
                    }),
                );
                await this.loadMenu();
                this.menuBulkSelectedIds = [];
                if (results.some((r) => !r)) {
                    void this.showUiAlert('Часть позиций не обновилась — проверьте сеть или обновите страницу', 'Ошибка');
                } else {
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
            await this.loadAnalytics();
            await this.$nextTick();
            setTimeout(() => {
                if (this.currentTab !== 'dashboard' || this.dashboardTab !== 'analytics') return;
                this._paintAnalyticsChartAfterLayout();
            }, 100);
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
                                    font: { size: 11 },
                                    color: '#64748b',
                                },
                                grid: { color: 'rgba(15, 23, 42, 0.06)', borderDash: [4, 4] },
                            },
                            x: {
                                ticks: { font: { size: 11 }, color: '#64748b' },
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
                            ticks: { font: { size: 11 }, maxRotation: 0 },
                        },
                        y: {
                            beginAtZero: true,
                            grid: { color: 'rgba(226, 232, 240, 0.9)' },
                            border: { display: false },
                            ticks: {
                                font: { size: 11 },
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

function adminApp() {
    return mergeAdminMixins(
        adminMixinState(),
        adminMixinMenuOrdersUi(),
        adminMixinSearchBookings(),
        adminMixinAuthKnowledge(),
        adminMixinPackagingIntegrationsDemoWsUi(),
        adminMixinWebSocketEvents(),
        adminMixinLiveChat(),
        adminMixinDataChartsSettings(),
    );
}

window.adminApp = adminApp;
