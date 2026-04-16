function onboardingApp() {
    return {
        step: 'signup',
        authenticated: false,
        username: '',
        organizationId: null,
        globalError: '',

        setupStatus: { score: 0, steps: [] },
        webhookUrl: '',

        signup: {
            restaurant_name: '',
            network_name: '',
            email: '',
            password: '',
        },
        signupLoading: false,

        iiko: {
            api_login: '',
            organizations: [],
            iiko_organization_id: '',
            terminal_group_id: '',
            lastStats: null,
        },
        iikoVerifyLoading: false,
        iikoSetupLoading: false,

        whatsapp: {
            phone_number_id: '',
            saved: false,
        },
        whatsappSaveLoading: false,

        goToStep(s) {
            this.globalError = '';
            this.step = s;
        },

        async init() {
            await this.refreshAuth();
            await this.reloadSetupStatus();
            if (this.authenticated) {
                await this.loadWebhookUrl();
                // если уже вошли — перескакиваем к iiko, чтобы не раздражать повторной регистрацией
                this.step = 'iiko';
                await this.loadOrgProfileIntoWhatsApp();
            }
        },

        async apiJsonResponse(url, opts = {}) {
            try {
                const res = await fetch(url, {
                    credentials: 'include',
                    ...opts,
                });
                const status = res.status;
                const data = await res.json().catch(() => ({}));
                return { ok: res.ok, status, data };
            } catch {
                return { ok: false, status: 0, data: { detail: 'Ошибка сети' } };
            }
        },

        formatApiError(detail) {
            if (!detail) return '';
            if (typeof detail === 'string') return detail;
            try {
                return JSON.stringify(detail);
            } catch {
                return String(detail);
            }
        },

        async refreshAuth() {
            const { ok, data } = await this.apiJsonResponse('/api/admin/auth/me');
            if (!ok) {
                this.authenticated = false;
                this.username = '';
                this.organizationId = null;
                return;
            }
            this.authenticated = true;
            this.username = data.username || '';
            this.organizationId = data.organization_id || null;
        },

        async reloadSetupStatus() {
            if (!this.authenticated) {
                this.setupStatus = { score: 0, steps: [] };
                return;
            }
            const { ok, data } = await this.apiJsonResponse('/api/admin/setup-status');
            if (ok && data) {
                this.setupStatus = data;
            }
        },

        async loadWebhookUrl() {
            const { ok, data } = await this.apiJsonResponse('/api/admin/integrations/status');
            if (ok && data && data.webhook_url) {
                this.webhookUrl = String(data.webhook_url);
            } else {
                this.webhookUrl = '';
            }
        },

        async copyWebhookUrl() {
            const t = (this.webhookUrl || '').trim();
            if (!t) return;
            try {
                await navigator.clipboard.writeText(t);
            } catch {
                // fallback: best-effort
                const inp = document.createElement('input');
                inp.value = t;
                document.body.appendChild(inp);
                inp.select();
                document.execCommand('copy');
                inp.remove();
            }
        },

        async submitSignup() {
            this.globalError = '';
            const payload = {
                restaurant_name: (this.signup.restaurant_name || '').trim(),
                network_name: (this.signup.network_name || '').trim(),
                email: (this.signup.email || '').trim(),
                password: this.signup.password || '',
            };
            if (!payload.restaurant_name || !payload.email || !payload.password) {
                this.globalError = 'Заполните название ресторана, email и пароль';
                return;
            }
            this.signupLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/auth/signup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!ok) {
                    this.globalError = this.formatApiError(data.detail) || 'Не удалось создать аккаунт';
                    return;
                }
                await this.refreshAuth();
                await this.reloadSetupStatus();
                await this.loadWebhookUrl();
                this.step = 'iiko';
            } finally {
                this.signupLoading = false;
            }
        },

        async verifyIiko() {
            this.globalError = '';
            const api_login = (this.iiko.api_login || '').trim();
            if (!api_login) {
                this.globalError = 'Вставьте ключ iiko Cloud';
                return;
            }
            this.iikoVerifyLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/integrations/iiko/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ api_login }),
                });
                if (!ok) {
                    this.globalError = this.formatApiError(data.detail) || 'Не удалось проверить ключ';
                    return;
                }
                this.iiko.organizations = Array.isArray(data.organizations) ? data.organizations : [];
                if (this.iiko.organizations.length === 1) {
                    this.iiko.iiko_organization_id = this.iiko.organizations[0].id || '';
                }
            } finally {
                this.iikoVerifyLoading = false;
            }
        },

        async setupIiko() {
            this.globalError = '';
            const api_login = (this.iiko.api_login || '').trim();
            const iiko_organization_id = (this.iiko.iiko_organization_id || '').trim();
            if (!api_login || !iiko_organization_id) {
                this.globalError = 'Выберите филиал и убедитесь, что ключ заполнен';
                return;
            }
            this.iikoSetupLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/integrations/iiko/setup', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        api_login,
                        iiko_organization_id,
                        terminal_group_id: (this.iiko.terminal_group_id || '').trim(),
                    }),
                });
                if (!ok) {
                    this.globalError = this.formatApiError(data.detail) || 'Не удалось подключить iiko';
                    return;
                }
                this.iiko.lastStats = data.stats || null;
                await this.reloadSetupStatus();
                this.step = 'whatsapp';
                await this.loadOrgProfileIntoWhatsApp();
            } finally {
                this.iikoSetupLoading = false;
            }
        },

        async loadOrgProfileIntoWhatsApp() {
            if (!this.authenticated) return;
            const { ok, data } = await this.apiJsonResponse('/api/admin/organization/profile');
            if (ok && data) {
                this.whatsapp.phone_number_id = (data.whatsapp_phone_number_id || '').trim();
            }
        },

        async saveWhatsApp() {
            this.globalError = '';
            this.whatsapp.saved = false;
            this.whatsappSaveLoading = true;
            try {
                const { ok, data } = await this.apiJsonResponse('/api/admin/organization/profile', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        whatsapp_phone_number_id: (this.whatsapp.phone_number_id || '').trim(),
                    }),
                });
                if (!ok) {
                    this.globalError = this.formatApiError(data.detail) || 'Не удалось сохранить';
                    return;
                }
                this.whatsapp.saved = true;
                await this.reloadSetupStatus();
                this.step = 'done';
            } finally {
                this.whatsappSaveLoading = false;
            }
        },
    };
}

