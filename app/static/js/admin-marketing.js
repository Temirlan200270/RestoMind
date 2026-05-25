'use strict';

/** Маркетинг (рассылки + лояльность) — lazy chunk для admin.html. */
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
