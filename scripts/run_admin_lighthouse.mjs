/**
 * Lighthouse (mobile) по экранам админки с сессией cookie.
 *
 * Требуется запущенный сервер (по умолчанию http://127.0.0.1:8000).
 * Авторизация: ADMIN_USERNAME + ADMIN_PASSWORD в env, иначе кнопка «Посмотреть демо».
 *
 * Запуск: npm run lh:admin
 * Env: LH_BASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { launch as launchChrome } from 'chrome-launcher';
import lighthouse from 'lighthouse';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, '..');
const OUT_DIR = path.join(ROOT, 'docs', 'ui', 'lighthouse');
const REPORTS_DIR = path.join(OUT_DIR, 'reports');

const BASE = (process.env.LH_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

/** Подхват ADMIN_* из корневого `.env`, если переменные не заданы в shell. */
function loadRootEnv() {
    try {
        const p = path.join(ROOT, '.env');
        if (!fs.existsSync(p)) return;
        for (const line of fs.readFileSync(p, 'utf8').split('\n')) {
            const t = line.trim();
            if (!t || t.startsWith('#')) continue;
            const eq = t.indexOf('=');
            if (eq < 1) continue;
            const key = t.slice(0, eq).trim();
            let val = t.slice(eq + 1).trim();
            if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
                val = val.slice(1, -1);
            }
            if (key === 'ADMIN_PASSWORD' && !process.env.ADMIN_PASSWORD) process.env.ADMIN_PASSWORD = val;
            if (key === 'ADMIN_USERNAME' && !process.env.ADMIN_USERNAME) process.env.ADMIN_USERNAME = val;
        }
    } catch {
        /* ignore */
    }
}

/** Основные экраны + все 8 подвкладок настроек (Phase U4 DoD). */
const PAGES = [
    { id: 'dashboard', path: '/admin#dashboard' },
    { id: 'orders', path: '/admin#orders' },
    { id: 'menu', path: '/admin#menu' },
    { id: 'settings_restaurant', path: '/admin#settings/restaurant' },
    { id: 'settings_branding', path: '/admin#settings/branding' },
    { id: 'settings_connections', path: '/admin#settings/connections' },
    { id: 'settings_smart_sales', path: '/admin#settings/smart_sales' },
    { id: 'settings_team', path: '/admin#settings/team' },
    { id: 'settings_health', path: '/admin#settings/health' },
    { id: 'settings_technical', path: '/admin#settings/technical' },
    { id: 'settings_bot_test', path: '/admin#settings/bot_test' },
];

function scorePct(lhr, catId) {
    const s = lhr?.categories?.[catId]?.score;
    if (s == null || Number.isNaN(s)) return null;
    return Math.round(s * 100);
}

async function obtainSessionCookie() {
    loadRootEnv();
    const adminUser = process.env.ADMIN_USERNAME || 'admin';
    const adminPass = process.env.ADMIN_PASSWORD || '';

    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext();
    const page = await context.newPage();
    const loginUrl = `${BASE}/admin`;
    await page.goto(loginUrl, { waitUntil: 'networkidle', timeout: 90000 });

    const sidebar = page.locator('nav.admin-sidebar-scroll');
    if (!(await sidebar.isVisible().catch(() => false))) {
        const demoBtn = page.locator('form button[type="button"]').first();
        const submitBtn = page.locator('form button[type="submit"]').first();
        await demoBtn.or(submitBtn).first().waitFor({ state: 'visible', timeout: 60000 });

        if (adminPass) {
            await page.locator('input[autocomplete="username"]').fill(adminUser);
            await page.locator('input[autocomplete="current-password"]').fill(adminPass);
            const loginRespP = page.waitForResponse(
                (r) => r.url().includes('/api/admin/auth/login') && r.request().method() === 'POST',
                { timeout: 45000 },
            );
            await submitBtn.click();
            const loginResp = await loginRespP;
            if (!loginResp.ok()) {
                const body = await loginResp.text();
                throw new Error(`POST /auth/login → ${loginResp.status()}: ${body.slice(0, 500)}`);
            }
        } else {
            const demoRespP = page.waitForResponse(
                (r) => r.url().includes('/api/admin/auth/demo-login') && r.request().method() === 'POST',
                { timeout: 45000 },
            );
            await demoBtn.click();
            const demoResp = await demoRespP;
            if (!demoResp.ok()) {
                const body = await demoResp.text();
                throw new Error(
                    `POST /auth/demo-login → ${demoResp.status()}: ${body.slice(0, 500)}. Задайте ADMIN_PASSWORD в .env`,
                );
            }
        }
    }

    await sidebar.waitFor({ state: 'visible', timeout: 90000 });
    const cookies = await context.cookies();
    await browser.close();

    const host = new URL(BASE).hostname;
    const parts = cookies
        .filter((c) => {
            const d = (c.domain || '').replace(/^\./, '');
            return d === host || host.endsWith(d);
        })
        .map((c) => `${c.name}=${c.value}`);
    return parts.join('; ');
}

async function run() {
    fs.mkdirSync(REPORTS_DIR, { recursive: true });

    let cookieHeader;
    try {
        cookieHeader = await obtainSessionCookie();
    } catch (e) {
        console.error(
            '[lh:admin] Не удалось войти в админку.\n' +
                '  1) Запустите: python -m uvicorn app.main:app --reload\n' +
                '  2) Укажите в `.env` или в shell: ADMIN_USERNAME, ADMIN_PASSWORD (как для входа в админку).\n' +
                '     Кнопка «Посмотреть демо» используется только если пароль не задан — на пустой БД demo-login может быть недоступен.\n',
            e,
        );
        process.exit(1);
    }

    if (!cookieHeader.includes('restomind_admin=')) {
        console.warn('[lh:admin] Предупреждение: cookie restomind_admin не найден — отчёты могут быть для экрана входа.');
    }

    const chrome = await launchChrome({ chromeFlags: ['--headless=new', '--no-sandbox', '--disable-gpu'] });

    const summary = {
        generatedAt: new Date().toISOString(),
        baseUrl: BASE,
        formFactor: 'mobile',
        cookieAuth: true,
        pages: [],
    };

    try {
        for (const { id, path: p } of PAGES) {
            const url = `${BASE}${p}`;
            process.stdout.write(`[lh:admin] ${id} … `);
            const runner = await lighthouse(url, {
                port: chrome.port,
                logLevel: 'error',
                output: 'json',
                onlyCategories: ['performance', 'accessibility', 'best-practices'],
                formFactor: 'mobile',
                screenEmulation: {
                    mobile: true,
                    width: 412,
                    height: 915,
                    deviceScaleFactor: 2.625,
                    disabled: false,
                },
                throttling: {
                    rttMs: 150,
                    throughputKbps: 1638.4,
                    cpuSlowdownMultiplier: 4,
                },
                extraHeaders: { Cookie: cookieHeader },
            });

            const lhr = JSON.parse(runner.report);
            const row = {
                id,
                url,
                performance: scorePct(lhr, 'performance'),
                accessibility: scorePct(lhr, 'accessibility'),
                bestPractices: scorePct(lhr, 'best-practices'),
            };
            summary.pages.push(row);
            fs.writeFileSync(path.join(REPORTS_DIR, `${id}.json`), runner.report, 'utf8');
            console.log(`A11y ${row.accessibility ?? '—'} / Perf ${row.performance ?? '—'} / BP ${row.bestPractices ?? '—'}`);
        }
    } finally {
        try {
            await chrome.kill();
        } catch (e) {
            console.warn('[lh:admin] Chrome cleanup warning:', e?.message || e);
        }
    }

    fs.writeFileSync(path.join(OUT_DIR, 'summary.json'), JSON.stringify(summary, null, 2), 'utf8');
    writeReadme(summary);
    console.log(`[lh:admin] Готово: ${path.join('docs', 'ui', 'lighthouse', 'summary.json')}`);
}

function writeReadme(summary) {
    const lines = [
        '# Lighthouse — админка (mobile)',
        '',
        'Автоматический прогон Lighthouse по авторизованным URL (профиль **mobile**).',
        '',
        '## Как запустить',
        '',
        '```bash',
        'npm install',
        'npx playwright install chromium',
        'python -m uvicorn app.main:app --reload   # отдельный терминал',
        'npm run lh:admin',
        '```',
        '',
        'Переменные: `LH_BASE_URL` (по умолчанию `http://127.0.0.1:8000`), `ADMIN_USERNAME`, `ADMIN_PASSWORD` (если пароль пуст — **«Посмотреть демо»**).',
        '',
        'Артефакты: **`summary.json`**, таблица ниже, полные отчёты в **`reports/`** (в `.gitignore`).',
        '',
        '---',
        '',
        `## Сводка (сгенерировано ${summary.generatedAt})`,
        '',
        `База: \`${summary.baseUrl}\` · профиль: **mobile**`,
        '',
        '| Экран | Performance | Accessibility | Best practices |',
        '|--------|------------:|--------------:|---------------:|',
    ];

    for (const p of summary.pages) {
        lines.push(
            `| ${p.id} | ${p.performance ?? '—'} | ${p.accessibility ?? '—'} | ${p.bestPractices ?? '—'} |`,
        );
    }

    lines.push(
        '',
        '**Интерпретация:** [UI_DESIGN_SYSTEM.md](../UI_DESIGN_SYSTEM.md) — целевые пороги и подход (a11y/Lighthouse). Баллы зависят от данных БД и окружения.',
        '',
        '## Текущий mobile baseline',
        '',
        'Accessibility уже приведена к рабочему уровню на большинстве экранов; оставшиеся просадки Performance — известный архитектурный долг, а не финальное целевое состояние. Главный bottleneck по отчётам: один большой `admin.html` (~550 KiB HTML), Alpine инициализирует скрытые `x-show` деревья всех вкладок, из-за чего растут DOM size, script evaluation и style/layout cost.',
        '',
        'Что уже вынесено из initial render: Chart.js загружается лениво, Alpine отдается локально, Google Fonts снят с critical path, Dashboard не догружает заказы на `ws_ready`, длинные блоки настроек ресторана грузятся по видимости/клику.',
        '',
        'Следующий крупный шаг к Performance ≥ 80: разделить админку на route/partial chunks или заменить тяжелые `x-show` вкладки на mount-on-demand (`x-if`/динамические partials), чтобы мобильный экран не создавал DOM для таблиц, канбана, аналитики, настроек и модалок одновременно.',
        '',
    );

    fs.writeFileSync(path.join(OUT_DIR, 'README.md'), lines.join('\n'), 'utf8');
}

run().catch((e) => {
    console.error(e);
    process.exit(1);
});
