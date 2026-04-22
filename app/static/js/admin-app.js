/**
 * RestoMind admin panel — Alpine x-data="adminApp()".
 * Подключать после Alpine и Chart.js.
 */
'use strict';

/**
 * Экземпляры Chart.js вне реактивного объекта Alpine — иначе Proxy может ломать внутренние ссылки на canvas.
 */
const charts = {
    dashboard: null,
    analytics: null,
    analyticsSparks: {},
};

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
 * Фрагмент админки в location.hash: #chats или #chats?phone=7705… (как в ссылках из Telegram).
 * @returns {{ tab: string | null, phone: string | null }}
 */
function adminParseLocationHash() {
    const raw = String(window.location.hash || '').replace(/^#/, '').trim();
    if (!raw) return { tab: null, phone: null };
    const q = raw.indexOf('?');
    const path = (q >= 0 ? raw.slice(0, q) : raw).trim();
    const qs = q >= 0 ? raw.slice(q + 1) : '';
    let phone = null;
    try {
        const sp = new URLSearchParams(qs);
        const p = (sp.get('phone') || '').trim();
        phone = p || null;
    } catch (_e) {
        phone = null;
    }
    return { tab: path || null, phone };
}

/** Начальное состояние GET /integrations/status — чтобы Alpine не падал на undefined до первой загрузки. */
function defaultIntegrationStatus() {
    return {
        iiko_configured: false,
        whatsapp_configured: false,
        openai_configured: false,
        whatsapp_voice_replies_enabled: false,
        webhook_url: '',
        whatsapp_verify_token_hint: '',
        last_stoplist: { at: null, ok: false, error: null },
        last_menu_sync: { at: null, ok: false, error: null },
        iiko_secrets_encrypt_ready: false,
        prepayment_enforced: true,
    };
}

/** Поля состояния (вкладки, сущности, UI) */
function adminMixinState() {
    return {
        // Авторизация
        authenticated: false,
        loginUsername: '',
        loginPassword: '',
        loginError: '',
        loginLoading: false,
        /** Показали ли уже модалку «сессия истекла» для серии 401 (не хранить на window). */
        auth401AlertShown: false,
        wsToken: '',
        hasDemoData: false,
        demoActionLoading: false,
        showDemoDeleteModal: false,
        demoDeleteAck: false,
        demoDeleteError: '',
        demoToastMessage: '',

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
        settingsEnv: null,
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
        packagingLoading: false,

        /** Филиал: автоматическая предоплата по порогу (см. PATCH /organization/prefs). */
        orgPrepaymentEnforcedSaving: false,

        knowledgeItems: [],
        knowledgeLoading: false,
        knowledgeSaveLoading: false,
        knowledgeEditOpen: false,
        knowledgeEditError: '',
        knowledgeEditForm: {
            id: null,
            category: '',
            question: '',
            answer: '',
            is_active: true,
            sort_order: 0,
        },

        currentTab: 'dashboard',
        /** Загрузка данных при смене вкладки (избегаем общего имени `loading` — конфликт миксинов). */
        tabDataLoading: false,
        /** Кэш сортировки таблицы «по дням» на аналитике (геттер не пересортировывает на каждый тик Alpine). */
        _analyticsDailySig: '',
        _analyticsDailySortedCache: [],
        /** Инкремент при loadAnalytics — сигнатура сортировки без тяжёлого map/join по всем дням. */
        analyticsDailyDataRev: 0,
        /** Заголовки секций сайдбара (один x-for в шаблоне). */
        navSections: [
            { id: 'overview', title: 'Обзор' },
            { id: 'operations', title: 'Операции' },
            { id: 'settings', title: 'Управление' },
        ],
        /** Вкладка внутри Settings (Stripe-like). */
        settingsTab: 'restaurant', // restaurant | connections | smart_sales | team | technical
        navItems: [
            { id: 'dashboard', section: 'overview', label: 'Дашборд', desc: 'Общая статистика и последние заказы',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25A2.25 2.25 0 018.25 10.5H6A2.25 2.25 0 013.75 8.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z"/></svg>' },
            { id: 'analytics', section: 'overview', label: 'Аналитика', desc: 'Выручка, средний чек, динамика продаж',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z"/></svg>' },
            { id: 'orders', section: 'operations', label: 'Заказы', desc: 'По этапам (черновик → подтверждён → кухня) или общий список',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.25 7.5l-.625 10.632a2.25 2.25 0 01-2.247 2.118H6.622a2.25 2.25 0 01-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z"/></svg>' },
            { id: 'operator_queue', section: 'operations', label: 'Помощь клиентам', desc: 'Обращения, где нужен человек (цель — «пусто»)',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"/></svg>' },
            { id: 'bookings', section: 'operations', label: 'Бронирования', desc: 'Столики и резервации',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 012.25-2.25h13.5A2.25 2.25 0 0121 7.5v11.25m-18 0A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75m-18 0v-7.5A2.25 2.25 0 015.25 9h13.5A2.25 2.25 0 0121 11.25v7.5"/></svg>' },
            { id: 'chats', section: 'operations', label: 'Диалоги', desc: 'Сообщения и ответы клиентам',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a21.05 21.05 0 00-1.889-2.403 19.7 19.7 0 00-1.6-1.562c-.642-.522-1.397-.957-2.23-1.25C16.247 1.872 14.747 1.5 12 1.5c-2.747 0-4.247.372-5.63.99-.833.293-1.588.728-2.23 1.25-.563.459-1.082 1-1.6 1.562A21.05 21.05 0 003.75 8.511"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M5.25 8.511c-.884.284-1.5 1.128-1.5 2.097v4.286c0 1.136.847 2.1 1.98 2.193.34.027.68.052 1.02.072v3.091l3-3a11.63 11.63 0 014.02-.163 2.115 2.115 0 001.825-.242M9.378 5.378A21.05 21.05 0 0018.72 3.728"/></svg>' },
            { id: 'menu', section: 'operations', label: 'Меню', desc: 'Позиции меню ресторана',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 8.25v-1.5m0 1.5c-1.355 0-2.697.056-4.024.166C6.845 8.51 6 9.473 6 10.608v2.513m6-4.871c1.355 0 2.697.055 4.024.165C17.155 8.51 18 9.473 18 10.608v2.513m-3 4.73v-1.59c0-.532-.21-1.042-.586-1.418L12 13.5m-3 4.73c.55.47 1.27.73 2 .73h6c.73 0 1.45-.26 2-.73m-8-4.73V10.6c0-1.12.856-2.08 2.09-2.19.64-.09 1.29-.14 1.91-.14m5 6.37v1.59c0 1.632-.875 3.11-2.25 3.89"/></svg>' },
            { id: 'stoplist', section: 'operations', label: 'Стоп-лист', desc: 'Нет в наличии и синхронизация с iiko',
              icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>' },
            { id: 'settings', section: 'settings', label: 'Настройки', desc: 'Ресторан, подключения, продажи, команда',
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
        /** Контекст текущего заведения (multi-tenant брендинг в шапке). */
        orgProfile: {
            id: null,
            organization_id: null,
            name: '',
            timezone: '',
            currency: '',
            whatsapp_phone_number_id: '',
            telegram_ops_chat_id: '',
        },
        orgProfileLoading: false,
        orgProfileSaving: false,
        orders: [],
        _ordersLoadSeq: 0,
        ordersLoadError: '',
        ordersPage: 1,
        ordersSize: 50,
        ordersPages: 1,
        ordersTotal: 0,
        ordersHasMore: false,
        bookings: [],
        menuItems: [],

        // Заказы
        ordersView: 'kanban',
        orderFilter: '',
        /** Поиск и фильтр суммы (список заказов) */
        orderSearchQ: '',
        orderSumMin: '',
        orderSumMax: '',
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
        setupStatus: { score: 0, steps: [], menu_items: 0, upsell_rules: 0 },
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
            price: 0,
            is_available: true,
            image_url: '',
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

        get kanbanDraft() {
            return this.orders.filter(o => o.status === 'draft');
        },
        get kanbanConfirmed() {
            return this.orders.filter(o => o.status === 'confirmed');
        },
        get kanbanSent() {
            return this.orders.filter(o => o.status === 'sent_to_iiko');
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
            const list = [...(this.orders || [])];
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
            return list;
        },

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
            } catch (_e) {
                this.menuCategoryChipsOpen = true;
                this.menuToolbarExpanded = true;
            }

            await this.checkSession();

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
                if (tab === 'dashboard' && charts.dashboard) {
                    charts.dashboard.resize();
                    charts.dashboard.update('none');
                }
                if (tab === 'analytics' && charts.analytics) {
                    charts.analytics.resize();
                    charts.analytics.update('none');
                }
            } catch (e) {
                console.warn('Chart resize', e);
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

        /** Человекочитаемая «причина» шага для персонала. */
        salesInsightWhy(trace) {
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
            this.$nextTick(() => {
                try {
                    this.initOrderRebuildFromSelected();
                    this.syncOrderPaymentFormFromSelected();
                } catch (_e) { /* ignore */ }
            });
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

        async confirmAndDeleteOrdersFromModal() {
            const o = this.selectedOrder;
            if (!o || o.id == null) return;
            await this.confirmAndDeleteOrders([Number(o.id)], 'modal');
        },

        /** Двойное подтверждение и удаление заказа(ов) через uiConfirm (без отдельной разметки модалки). */
        async confirmAndDeleteOrders(ids, source) {
            const clean = [...new Set((ids || []).map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0))].sort((a, b) => a - b);
            if (!clean.length) return;
            const src = source || '';
            const msg1 = clean.length === 1
                ? `Заказ #${clean[0]} будет удалён из базы без возможности восстановления. Запись пропадёт из списков и аналитики.`
                : `Будут безвозвратно удалены заказы (${clean.length} шт.): ${clean.map((i) => '#' + i).join(', ')}.`;
            let r = await this.openUiConfirm({
                title: clean.length === 1 ? 'Удалить заказ?' : 'Удалить заказы?',
                message: msg1,
                danger: true,
                confirmText: 'Продолжить',
            });
            if (!r.ok) return;
            r = await this.openUiConfirm({
                message: 'Последнее подтверждение: отменить это действие будет нельзя.',
                danger: true,
                confirmText: 'Удалить навсегда',
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
                    this.demoToastMessage = `Заказ #${id} удалён`;
                    setTimeout(() => { this.demoToastMessage = ''; }, 3500);
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
                    this.demoToastMessage = `Удалено заказов: ${data.deleted ?? ids.length}`;
                    setTimeout(() => { this.demoToastMessage = ''; }, 4000);
                }
                this.removeOrderIdsFromLocalState(ids);
                try {
                    await Promise.all([this.loadOrders(), this.loadDashStats(), this.loadSettingsOrders()]);
                    await this.syncDashboardChartIfVisible();
                } catch (refreshErr) {
                    console.error('[admin] обновление списков после удаления заказа', refreshErr);
                }
            } catch (e) {
                console.error('[admin] _executeOrderDeleteDirect', e);
                await this.openUiConfirm({
                    title: 'Ошибка',
                    message: 'Ошибка сети. Проверьте соединение.',
                    showCancel: false,
                    confirmText: 'Понятно',
                });
            }
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
            }
            if (e.key === 'Escape') {
                this.globalSearchOpen = false;
                if (this.knowledgeEditOpen) this.closeKnowledgeEdit();
            }
        },

        openGlobalSearch() {
            this.globalSearchOpen = true;
            this.globalSearchQ = '';
            this.globalSearchLastFetchedQ = '';
            this.globalSearchResults = { orders: [], chats: [], bookings: [] };
            this.$nextTick(() => {
                const el = document.getElementById('global-search-input');
                if (el) el.focus();
            });
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
                console.error(e);
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
                this.demoToastMessage = 'Телефон скопирован';
                setTimeout(() => { this.demoToastMessage = ''; }, 2500);
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
                this.demoToastMessage = 'URL скопирован';
                setTimeout(() => { this.demoToastMessage = ''; }, 2500);
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
            this.orgProfile = {
                id: null,
                organization_id: null,
                name: '',
                timezone: '',
                currency: '',
                whatsapp_phone_number_id: '',
                telegram_ops_chat_id: '',
            };
            this.orgProfileLoading = false;
            this.integrationStatus = defaultIntegrationStatus();
            this.integrationEvents = [];
            this.hasDemoData = false;
            this.menuViewRevision += 1;
        },

        async checkSession() {
            try {
                const res = await this.apiFetch('/api/admin/auth/me');
                if (res.ok) {
                    const data = await res.json();
                    this.authenticated = true;
                    this.auth401AlertShown = false;
                    this.wsToken = data.ws_token || '';
                    this._ensureAdminHashListener();
                    this._applyAdminHashBeforeFirstPaint();
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
                this.auth401AlertShown = false;
                this.authenticated = true;
                this.wsToken = data.ws_token || '';
                this.loginPassword = '';
                this._ensureAdminHashListener();
                this._applyAdminHashBeforeFirstPaint();
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
                    console.warn('GET /api/admin/organization/profile', status, data);
                    return;
                }
                this.orgProfile = {
                    id: data?.id ?? null,
                    organization_id: data?.organization_id ?? null,
                    name: (data?.name || '').trim(),
                    timezone: (data?.timezone || '').trim(),
                    currency: (data?.currency || '').trim(),
                    whatsapp_phone_number_id: (data?.whatsapp_phone_number_id || '').trim(),
                    telegram_ops_chat_id: (data?.telegram_ops_chat_id || '').trim(),
                };
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
                const body = {
                    name: nm,
                    timezone: String(this.orgProfile?.timezone || '').trim(),
                    currency: String(this.orgProfile?.currency || '').trim(),
                    whatsapp_phone_number_id: String(this.orgProfile?.whatsapp_phone_number_id || '').trim(),
                    telegram_ops_chat_id: String(this.orgProfile?.telegram_ops_chat_id || '').trim(),
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
                };
            } finally {
                this.orgProfileSaving = false;
            }
        },

        async logoutAdmin() {
            try {
                if (this.ws) {
                    this.ws.close();
                    this.ws = null;
                }
                await this.apiFetch('/api/admin/auth/logout', { method: 'POST' });
            } catch { /* ignore */ }
            this.authenticated = false;
            this.wsToken = '';
            this.wsChannelReady = false;
            this._clearWsReadyTimer();
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
            this.integrationStatus = {
                ...base,
                ...rest,
                last_stoplist,
                last_menu_sync,
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
                console.error('[admin] onPrepaymentEnforcedToggle', e);
                void this.showUiAlert('Ошибка сети', 'Ошибка');
            } finally {
                this.orgPrepaymentEnforcedSaving = false;
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
                console.error('[admin] loadKnowledgeBase', e);
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
            this.knowledgeEditForm = {
                id: k.id,
                category: k.category || '',
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
                const payload = {
                    category: (f.category || '').trim(),
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
            } catch (e) {
                console.error('[admin] saveKnowledgeItem', e);
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
                if (res.ok) await this.loadKnowledgeBase();
                else {
                    const data = await res.json().catch(() => ({}));
                    void this.showUiAlert(this.formatApiError(data), 'Ошибка');
                }
            } catch (e) {
                console.error('[admin] deleteKnowledgeItem', e);
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
                console.error('[admin] packagingPreviewRun', e);
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
                console.error('[admin] loadPackagingRules', e);
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
                        is_active: rule.is_active, sort_order: rule.sort_order,
                    }),
                });
                if (!ok) void this.showUiAlert(this.formatApiError(d), 'Ошибка');
            } catch (e) { console.error('[admin] packagingSave', e); }
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
            } catch (e) { console.error('[admin] packagingDelete', e); }
            finally { rule._saving = false; }
        },
        async packagingAddNew() {
            const { ok, value } = await this.openUiConfirm({
                title: 'Новое правило упаковки',
                message: 'Уникальный ключ kind (латиница, например dessert).',
                confirmText: 'Добавить',
                showInput: true,
                input: {
                    label: 'kind',
                    placeholder: 'например dessert',
                    required: true,
                },
            });
            if (!ok) return;
            const kind = String(value || '').trim();
            if (!kind) return;
            try {
                const { ok, data: d } = await this.apiJsonResponse('/api/admin/packaging-rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ kind, name: 'Новое правило', price: 0, keywords: '', is_active: true, sort_order: 0 }),
                });
                if (ok) await this.loadPackagingRules();
                else void this.showUiAlert(this.formatApiError(d), 'Ошибка');
            } catch (e) { console.error('[admin] packagingAddNew', e); }
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
                    };
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
                console.error(e);
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
                console.error(e);
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
                console.error('[admin] loadUpsellRules', e);
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
                console.error('[admin] upsellAddRule', e);
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
            } catch (e) { console.error(e); }
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
            } catch (e) { console.error(e); }
        },

        async syncIntegrationsNow() {
            if (!this.integrationStatus.iiko_configured) return;
            this.integrationSyncLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/integrations/sync', { method: 'POST' });
                if (!ok) {
                    console.error('[admin] POST /integrations/sync', status, data);
                    void this.showUiAlert(this.formatApiError(data.detail) || 'Синхронизация не удалась', 'Ошибка');
                    return;
                }
                if (data.status) {
                    this.mergeIntegrationStatus(data.status);
                } else {
                    console.warn('[admin] ответ без status, подгружаем статус');
                    await this.loadIntegrationStatus();
                }
                const ev = await this.apiJsonResponse('/api/admin/integrations/events?limit=40');
                if (ev.ok) this.integrationEvents = ev.data.events || [];
                const mOk = data.menu && data.menu.ok;
                const sOk = data.stop_lists && data.stop_lists.ok;
                console.info('[admin] синхронизация iiko', { menu: data.menu, stop_lists: data.stop_lists });
                if (mOk && sOk) {
                    this.demoToastMessage = 'Меню и стоп-листы обновлены из iiko';
                } else if (mOk && !sOk) {
                    this.demoToastMessage = 'Меню обновлено; стоп-листы: ошибка (см. журнал ниже)';
                } else if (!mOk && sOk) {
                    this.demoToastMessage = 'Стоп-листы обновлены; меню: ошибка (см. журнал)';
                } else {
                    this.demoToastMessage = 'Синхронизация завершена с предупреждениями — см. журнал';
                }
                setTimeout(() => { this.demoToastMessage = ''; }, 5500);
                this.menuViewRevision += 1;
                await this.loadMenu();
                if (this.currentTab === 'stoplist') await this.loadStopList();
                await this.loadSetupStatus();
            } catch (e) {
                console.error('[admin] integrations/sync', e);
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
                    console.error('[admin] POST /stop-lists/sync', status, data);
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
                console.info('[admin] стоп-лист iiko', { stopped: st, restored: rs });
                this.demoToastMessage = `Стоп-лист iiko: в стоп ${st}, восстановлено ${rs}`;
                setTimeout(() => { this.demoToastMessage = ''; }, 5000);
                this.menuViewRevision += 1;
                await this.loadMenu();
                if (this.currentTab === 'stoplist') await this.loadStopList();
            } catch (e) {
                console.error('[admin] stop-lists/sync', e);
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
                this.demoToastMessage = `Стоп-лист: ${derived.length} поз.`;
                setTimeout(() => { this.demoToastMessage = ''; }, 4000);
                this._recalcStopListFiltered();
            } catch (e) {
                console.error('[admin] loadStopList', e);
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
                    this.demoToastMessage = `Меню из iiko: новых ${data.created ?? 0}, обновлено ${data.updated ?? 0}${sk}`;
                    setTimeout(() => { this.demoToastMessage = ''; }, 5000);
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
                        this.demoToastMessage = data.message;
                        setTimeout(() => { this.demoToastMessage = ''; }, 5000);
                    } else {
                        const m = data.menu_items_added ? `Меню: +${data.menu_items_added} поз.` : '';
                        const u = data.users_created != null ? `Пользователей: ${data.users_created}. ` : '';
                        const o = data.orders_added != null ? `Заказов: ${data.orders_added}. ` : '';
                        this.demoToastMessage = (u + o + m).trim() || 'Демо-данные загружены';
                        setTimeout(() => { this.demoToastMessage = ''; }, 5000);
                    }
                    await this.refreshDemoStatus();
                    try {
                        await this.loadTabData();
                    } catch (e) {
                        console.error('loadTabData после демо', e);
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
                    this.demoToastMessage = parts.length
                        ? `Демо удалено (${parts.join(', ')})`
                        : 'Демо-данные удалены';
                    setTimeout(() => { this.demoToastMessage = ''; }, 4500);
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
            // Не держим два сокета одновременно — иначе события (order_updated/new_message) будут дублироваться.
            try {
                const rs0 = this.ws?.readyState;
                if (rs0 === WebSocket.OPEN || rs0 === WebSocket.CONNECTING) {
                    this.ws.onopen = null;
                    this.ws.onclose = null;
                    this.ws.onerror = null;
                    this.ws.onmessage = null;
                    this.ws.close();
                }
            } catch (_e) { /* noop */ }
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            const url = `${proto}://${location.host}/api/admin/ws?token=${encodeURIComponent(this.wsToken)}`;

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
                console.log('[WS] Socket open, ждём ws_ready…');
            };

            this.ws.onclose = () => {
                this._clearWsReadyTimer();
                this.wsChannelReady = false;
                this.ws = null;
                this.wsEpoch++;
                console.log('[WS] Disconnected');
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
                } catch (e) { console.error('[WS] Parse error:', e); }
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
                if (this.currentTab === 'orders' || this.currentTab === 'dashboard') {
                    void this.loadOrders();
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
                if (this.currentTab === 'dashboard') {
                    void this.loadDashActivity();
                }
            } else if (type === 'new_message') {
                this.onNewMessage(data);
                this._pushDashLiveFeed(
                    type,
                    `${data.role === 'user' ? 'Клиент' : (data.role === 'operator' ? 'Оператор' : 'Бот')} · ${data.phone || ''}`,
                );
                if (this.currentTab === 'dashboard') {
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
                if (this.currentTab === 'dashboard') {
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
                if (this.currentTab === 'dashboard') {
                    void this.loadDashActivity();
                }
            } else if (type === 'human_needed') {
                this.onHumanNeeded(data);
                this._pushDashLiveFeed(type, `Нужен оператор · ${data.phone || ''}`);
                if (this.currentTab === 'dashboard') {
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
                console.warn('resend', status, data);
                return;
            }
            await this.selectChat(this.activeChatPhone);
        },

        // ─── Event Handlers ──────────────────────────
        onMessageStatusUpdated(data) {
            const id = Number(data.chat_log_id);
            if (!id) return;
            const row = this.chatMessages.find((m) => Number(m.id) === id);
            if (!row) return;
            row.delivery_status = data.delivery_status || row.delivery_status;
            if (data.provider_message_id) row.provider_message_id = data.provider_message_id;
            if (data.error_details !== undefined) row.error_details = data.error_details;
            row.status_updated_at = new Date().toISOString();
        },

        chatDeliveryMark(msg) {
            const s = String(msg.delivery_status || '').toLowerCase();
            if (!s || msg.role === 'user') return '';
            if (s === 'sending') return '\u231B';
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
                sending: 'Отправляется\u2026',
                sent: 'Отправлено',
                delivered: 'Доставлено',
                read: 'Прочитано',
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
            if (s === 'sending') cls = 'bg-white/15 text-white border-white/20';
            else if (s === 'sent') cls = 'bg-white/15 text-white border-white/20';
            else if (s === 'delivered' || s === 'read') cls = 'bg-emerald-500/20 text-white border-emerald-200/30';
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

        // ─── Alerts ──────────────────────────────────
        _playTone(type, freq, duration = 0.2, volume = 0.12) {
            try {
                const Ctx = window.AudioContext || window.webkitAudioContext;
                this._audioCtx = this._audioCtx || new Ctx();
                const ctx = this._audioCtx;
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

        /** Перед loadTabData: открыть нужную вкладку по hash (глубокая ссылка на диалог). */
        _applyAdminHashBeforeFirstPaint() {
            this._pendingHashChatPhone = null;
            const { tab, phone } = adminParseLocationHash();
            if (tab === 'chats') {
                this.currentTab = 'chats';
                if (phone) this._pendingHashChatPhone = phone;
            } else if (tab === 'orders') {
                this.currentTab = 'orders';
            }
        },

        syncAdminChatsHash(phone) {
            if (!this.authenticated) return;
            const path = window.location.pathname || '/admin';
            const frag = phone && String(phone).trim()
                ? `chats?phone=${encodeURIComponent(String(phone).trim())}`
                : 'chats';
            const url = `${path}#${frag}`;
            try {
                window.history.replaceState(null, '', url);
            } catch (_e) {
                try {
                    window.location.hash = frag;
                } catch (_e2) { /* ignore */ }
            }
        },

        async _consumePendingHashChatPhone() {
            const p = this._pendingHashChatPhone;
            this._pendingHashChatPhone = null;
            if (!p) return;
            await this.selectChat(p);
        },

        async _onAdminHashChange() {
            if (!this.authenticated) return;
            const { tab, phone } = adminParseLocationHash();
            if (tab === 'orders') {
                this.currentTab = 'orders';
                await this.loadTabData();
                return;
            }
            if (tab !== 'chats') return;
            this.currentTab = 'chats';
            await this.loadTabData();
            await this.loadChatList();
            if (phone) await this.selectChat(phone);
            else {
                this.activeChatPhone = '';
                this.chatMobileInfoOpen = false;
            }
        },

        // ─── Live Chat ───────────────────────────────
        async loadChatList(reset = true) {
            if (this.chatListLoading) return;
            if (!reset && !this.chatListHasMore) return;
            this.chatListLoading = true;
            try {
                const limit = 60;
                let url = `/api/admin/chats?limit=${limit}`;
                if (!reset) {
                    if (this.chatListCursorAt) url += `&cursor_at=${encodeURIComponent(String(this.chatListCursorAt))}`;
                    if (this.chatListCursorId) url += `&cursor_id=${encodeURIComponent(String(this.chatListCursorId))}`;
                }
                const res = await this.apiFetch(url);
                if (!res.ok) {
                    console.warn('GET /api/admin/chats', res.status);
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
                console.warn('loadChatList', e);
            } finally {
                this.chatListLoading = false;
            }
        },

        async loadMoreChats() {
            await this.loadChatList(false);
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
                console.error('loadCustomerSummary', e);
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
                console.error('saveCustomerNote', e);
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
                this.demoToastMessage = 'Не удалось изменить режим диалога. Проверьте соединение.';
                setTimeout(() => { this.demoToastMessage = ''; }, 4500);
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
                this.demoToastMessage = 'Не удалось перехватить диалог. Попробуйте ещё раз.';
                setTimeout(() => { this.demoToastMessage = ''; }, 4500);
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
                this.demoToastMessage = 'Не удалось вернуть ИИ. Попробуйте ещё раз.';
                setTimeout(() => { this.demoToastMessage = ''; }, 4500);
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
            // Legacy ids → Settings tabs (backward compatibility for old sidebar/hash links)
            const legacySettingsMap = {
                integrations: 'connections',
                packaging: 'restaurant',
                knowledge: 'restaurant',
                upsell: 'smart_sales',
                team: 'team',
                test: 'technical',
            };
            const legacyTab = legacySettingsMap[this.currentTab];
            if (legacyTab) {
                this.currentTab = 'settings';
                this.settingsTab = legacyTab;
            }
            if (this.currentTab !== 'chats') {
                this.chatMobileInfoOpen = false;
                this.activeChatPhone = '';
            }
            if (this.currentTab !== 'menu') {
                this.menuBulkMode = false;
                this.menuBulkSelectedIds = [];
            }
            this.tabDataLoading = true;
            try {
                if (this.currentTab === 'dashboard') {
                    await Promise.all([this.loadDashStats(), this.loadDashActivity(), this.loadOrders()]);
                } else if (this.currentTab === 'analytics') {
                    await this.loadAnalytics();
                } else if (this.currentTab === 'orders') {
                    await this.loadOrders();
                } else if (this.currentTab === 'operator_queue') {
                    await Promise.all([this.loadFailedTasks(), this.loadDashStats()]);
                } else if (this.currentTab === 'bookings') {
                    await this.loadBookings();
                } else if (this.currentTab === 'menu') {
                    await this.loadMenu();
                } else if (this.currentTab === 'stoplist') {
                    await Promise.all([this.loadStopList(), this.loadIntegrationStatus()]);
                } else if (this.currentTab === 'settings') {
                    if (this.settingsTab === 'connections') {
                        await this.loadIntegrationStatus();
                    } else if (this.settingsTab === 'smart_sales') {
                        await this.loadUpsellRules();
                    } else if (this.settingsTab === 'team') {
                        await this.loadTeam();
                    } else if (this.settingsTab === 'technical') {
                        await Promise.all([this.loadSettingsOrders(), this.loadSettingsEnvironment()]);
                    } else {
                        // restaurant
                        await Promise.all([this.loadOrgProfile(), this.loadKnowledgeBase(), this.loadPackagingRules()]);
                    }
                } else if (this.currentTab === 'chats') {
                    await this.loadChatList();
                }
            } catch (e) {
                console.error(e);
                void this.showUiAlert('Не удалось загрузить данные вкладки. Проверьте сеть и обновите страницу.', 'Ошибка');
            }
            if (this.currentTab !== 'dashboard' && charts.dashboard) {
                try {
                    charts.dashboard.destroy();
                } catch (_e) { /* ignore */ }
                charts.dashboard = null;
            }
            if (this.currentTab !== 'analytics' && charts.analytics) {
                try {
                    charts.analytics.destroy();
                } catch (_e) { /* ignore */ }
                charts.analytics = null;
            }
            if (this.currentTab !== 'analytics') {
                this._destroyAnalyticsSparklines();
            }
            this.tabDataLoading = false;
            // После layout (fade-in / flex) — отрисовка графиков не из реактивного цикла; пауза стабилизирует размер canvas.
            await this.$nextTick();
            const tab = this.currentTab;
            setTimeout(() => {
                if (this.currentTab !== tab) return;
                if (tab === 'dashboard') {
                    this.scheduleDashboardChartRender();
                }
                // Одна отрисовка аналитики после layout (loadAnalytics только грузит данные).
                if (tab === 'analytics') {
                    this._paintAnalyticsChartAfterLayout();
                }
                this._resizeVisibleCharts(tab);
            }, 100);
            [150, 400, 800].forEach((ms) => {
                setTimeout(() => this._resizeVisibleCharts(tab), ms);
            });
        },

        setSettingsTab(tab) {
            const allowed = new Set(['restaurant', 'connections', 'smart_sales', 'team', 'technical']);
            if (!allowed.has(String(tab || ''))) return;
            this.currentTab = 'settings';
            this.settingsTab = String(tab);
            this.loadTabData();
        },

        async loadDashStats() {
            const initial = !this.dashStatsLoadedOnce;
            if (initial) {
                this.dashStatsLoading = true;
            }
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/stats');
                if (!ok) {
                    console.warn('GET /api/admin/stats', status, data);
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

        async loadDashActivity() {
            this.dashActivityLoading = true;
            try {
                const { ok, status, data } = await this.apiJsonResponse('/api/admin/activity?limit=25');
                if (!ok) {
                    console.warn('GET /api/admin/activity', status, data);
                    return;
                }
                this.dashActivity = Array.isArray(data.items) ? data.items : [];
            } finally {
                this.dashActivityLoading = false;
            }
        },

        /**
         * Chart.js после x-show — контейнер мог быть 0×0; ждём 2× rAF перед созданием.
         * Возвращает Promise, чтобы loadDashStats дождался отрисовки (иначе resize в loadTabData иногда раньше графика).
         */
        scheduleDashboardChartRender() {
            return new Promise((resolve) => {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        try {
                            this.renderDashboardMiniChart();
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

        /** После изменения заказов: перерисовать мини-график, если открыт дашборд. */
        async syncDashboardChartIfVisible() {
            await this.$nextTick();
            if (this.currentTab === 'dashboard') {
                await this.scheduleDashboardChartRender();
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
                    console.error('[admin] scheduleDashStatsRefreshDebounced', e);
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
                    console.warn('[admin] loadFailedTasks', status, data);
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
                console.error(e);
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
                console.error('Меню: ошибка API', status, data);
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
                console.error('[admin] loadSettingsOrders', e);
                this.settingsOrdersList = [];
            } finally {
                this.settingsOrdersLoading = false;
            }
        },

        async loadSettingsEnvironment() {
            this.settingsEnvLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/settings/environment');
                this.settingsEnv = ok && data ? data : null;
            } catch (e) {
                console.error('[admin] loadSettingsEnvironment', e);
                this.settingsEnv = null;
            } finally {
                this.settingsEnvLoading = false;
            }
        },

        async settingsPurgeRedisSession() {
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
                this.demoToastMessage = `Сессия сброшена: ${phone}`;
                setTimeout(() => { this.demoToastMessage = ''; }, 3500);
            } catch (e) {
                console.error(e);
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
                console.error('[admin] export csv', e);
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
                this.demoToastMessage = `Отменено заказов: ${data.cancelled ?? 0}; уже были отменены: ${data.skipped_already_cancelled ?? 0}`;
                setTimeout(() => { this.demoToastMessage = ''; }, 4500);
                await Promise.all([this.loadSettingsOrders(), this.loadOrders(), this.loadDashStats(), this.loadSettingsEnvironment()]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                console.error('[admin] settingsBulkCancelOrders', e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsBulkCancelLoading = false;
            }
        },

        async settingsRunChatRetention() {
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
                this.demoToastMessage = `Удалено записей чата: ${data.deleted ?? 0}`;
                setTimeout(() => { this.demoToastMessage = ''; }, 4000);
                await this.loadSettingsEnvironment();
            } catch (e) {
                console.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsRetentionRunLoading = false;
            }
        },

        async settingsBulkDeleteOrders() {
            const ids = [...new Set(this.settingsSelectedOrderIds.map((x) => Number(x)).filter((n) => Number.isFinite(n) && n > 0))];
            if (!ids.length) return;
            await this.confirmAndDeleteOrders(ids, 'settings_bulk');
        },

        async settingsClearMenuOnly() {
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
                this.demoToastMessage = data.deleted != null ? `Меню: удалено позиций ${data.deleted}` : 'Меню очищено';
                setTimeout(() => { this.demoToastMessage = ''; }, 4000);
                await Promise.all([this.loadMenu(), this.loadDashStats()]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                console.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsMenuClearLoading = false;
            }
        },

        async settingsClearMenuAndStopSnapshot() {
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
                this.demoToastMessage = `Меню филиала очищено (${data.menu_items_deleted ?? 0} поз.)`;
                setTimeout(() => { this.demoToastMessage = ''; }, 4500);
                await Promise.all([
                    this.loadMenu(),
                    this.loadDashStats(),
                    this.loadStopList(),
                    this.loadIntegrationStatus(),
                ]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                console.error(e);
                void this.showUiAlert('Ошибка сети. Проверьте соединение.', 'Ошибка');
            } finally {
                this.settingsMenuStopClearLoading = false;
            }
        },

        async confirmSettingsPurgeOperational() {
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
                this.demoToastMessage = `Сброс: заказов ${data.orders_deleted ?? 0}, броней ${data.bookings_deleted ?? 0}, сообщений чата ${data.chat_logs_deleted ?? 0}`;
                setTimeout(() => { this.demoToastMessage = ''; }, 5000);
                await Promise.all([
                    this.loadSettingsOrders(),
                    this.loadOrders(),
                    this.loadBookings(),
                    this.loadDashStats(),
                    this.loadIntegrationStatus(),
                ]);
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                console.error(e);
                this.settingsPurgeError = 'Ошибка сети';
            } finally {
                this.settingsPurgeLoading = false;
            }
        },

        async clearAllMenuFromDb() {
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
                this.demoToastMessage = data.deleted != null
                    ? `Удалено позиций: ${data.deleted}`
                    : 'Меню очищено';
                setTimeout(() => { this.demoToastMessage = ''; }, 3200);
                await this.loadMenu();
                await this.loadDashStats();
                await this.syncDashboardChartIfVisible();
            } catch (e) {
                console.error('[admin] clearAllMenuFromDb', e);
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
                price: 0,
                is_available: true,
                image_url: '',
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
                price: Number(item.price) || 0,
                is_available: !!item.is_available,
                image_url: item.image_url || '',
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
                const payload = {
                    name,
                    category: String(f.category || '').trim(),
                    description: String(f.description || '').trim(),
                    price: Math.max(0, Number(f.price) || 0),
                    is_available: !!f.is_available,
                    image_url: String(f.image_url || '').trim() || null,
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
                    this.demoToastMessage = f.id == null ? 'Позиция добавлена' : 'Позиция сохранена';
                    setTimeout(() => { this.demoToastMessage = ''; }, 3000);
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
                    this.demoToastMessage = 'Позиция удалена';
                    setTimeout(() => { this.demoToastMessage = ''; }, 3000);
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
                    this.demoToastMessage = available ? 'Выбранные позиции в продаже' : 'Выбранные позиции в стопе';
                    setTimeout(() => { this.demoToastMessage = ''; }, 2800);
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
                    console.warn('GET /api/admin/analytics', status, raw);
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
        _paintAnalyticsChartAfterLayout() {
            try {
                this.renderChart();
                const canvas = document.getElementById('revenueChart');
                const parent = canvas?.parentElement;
                if (charts.analytics && parent) {
                    this._attachChartLayoutFix(charts.analytics, parent);
                }
            } catch (e) {
                console.warn('analytics chart paint', e);
            }
        },

        /** Смена периода на вкладке «Аналитика»: данные + один отложенный рендер графика. */
        async reloadAnalyticsForUi() {
            await this.loadAnalytics();
            await this.$nextTick();
            setTimeout(() => {
                if (this.currentTab !== 'analytics') return;
                this._paintAnalyticsChartAfterLayout();
            }, 100);
        },

        renderChart() {
            const canvas = document.getElementById('revenueChart');
            if (!canvas) return;
            const daily = this.analyticsData.daily || [];
            if (daily.length === 0) {
                if (charts.analytics) {
                    try {
                        charts.analytics.destroy();
                    } catch (_e) { /* ignore */ }
                    charts.analytics = null;
                }
                this._destroyAnalyticsSparklines();
                return;
            }

            const ctx = canvas.getContext('2d');
            if (charts.analytics) {
                try {
                    charts.analytics.destroy();
                } catch (_e) { /* ignore */ }
                charts.analytics = null;
            }

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
                console.error('Chart.js (аналитика):', e);
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
        renderDashboardMiniChart() {
            if (this.currentTab !== 'dashboard') return;
            const canvas = document.getElementById('dashboardHeroChart');
            if (!canvas) return;
            const series = this.dashStats.daily_series || [];
            // Пустая серия: не вызываем destroy() — иначе график исчезает навсегда до следующего успешного /stats.
            if (series.length === 0) return;

            const ctx = canvas.getContext('2d');
            if (charts.dashboard) {
                try {
                    charts.dashboard.destroy();
                } catch (_e) { /* ignore */ }
                charts.dashboard = null;
            }

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
                console.error('Chart.js (дашборд):', e);
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
