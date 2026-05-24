# Demo Pitch — 30-секундная продажа (G10.8 / G10.8.1)

Канонический документ по **sales demo** для владельца/инвестора. Статус задач — только [`ROADMAP.md`](ROADMAP.md) § G10.8.

## Короткий ответ: «идеальное демо»?

**Для продажи через counterfactual pitch — да, production-ready v1.**  
**Для «0 слов, 0 логина, landing autoplay» — ещё нет** (см. [Что не идеально](#что-не-идеально-следующий-уровень)).

| Критерий | Статус |
|----------|--------|
| 30 сек scripted pitch (боль → counterfactual → спасение → поток → закрепление) | ✅ |
| Один риск на экран, shift-only, без навигации во время pitch | ✅ |
| Counterfactual «без системы → потеря / с системой → спасено» | ✅ |
| Live Impact + micro-flash −1200 → +1200 ₸ | ✅ |
| Read-only demo session + explore после Esc | ✅ |
| Seed с «живой» очередью после pitch (не 90k «всё горит») | ✅ |
| Вход без формы логина / autoplay с landing | ❌ G10.8.2 |
| Self-demo для cold outreach (публичная ссылка) | ❌ G10.8.2 |

---

## Два режима

| Режим | Когда | Источник данных | Мутации |
|-------|-------|-----------------|--------|
| **Pitch** | 30 сек autoplay после «Посмотреть демо» | `GET /api/admin/demo/shift-scene/{id}/state?phase=` — canned JSON | Нет |
| **Explore** | Esc / «Осмотреть демо» / после pitch | `GET /api/admin/shift/state` + seed `demo7700…` | POST заблокирован (403) |

Pitch **не пишет в БД**. Explore — read-only walkthrough по seed demo-org (создаётся/обновляется **при старте приложения**, см. `app/main.py`).

---

## User flow

```
Login → «Посмотреть демо»
  → POST /api/admin/auth/demo-login (read-only session)
  → autoplay money_rescue_30s (~30 сек)
  → Esc или «Осмотреть демо» на resolve-карточке
  → explore: смена, inbox, дашборд (cap риска ~12k ₸)
  → ↻ «Повторить» — restart pitch без reload explore-state
```

Кнопка на login: **«Посмотреть демо»** (подпись: «30 сек — как теряются и возвращаются деньги…»).

---

## Тайминг pitch (`money_rescue_30s`)

| Сек | Phase | Что видит владелец |
|-----|-------|-------------------|
| 0–5 | `hook` | «Клиент уже почти ушёл…», wait timer, risk increasing |
| 5–10 | `tension` | Counterfactual banner: «Было бы потеряно 1 200 ₸», urgency countdown |
| 10–15 | `action` | «✔ Ответ отправлен автоматически» → choreo exit |
| 15–20 | `impact` | Live Impact: −1200 (flash) → «Клиент возвращён» → «+1 200 ₸ спасено» + tick |
| 20–25 | `next` | «Следующий риск: 2 клиента ждут ответа» |
| 25–30 | `resolve` | «Система автоматически спасает…» + stat + CTA |

Фазы синхронизированы: `demo_shift_scene.py` ↔ `DEMO_SHIFT_SCENE_PHASES` в `admin-app.js`.

---

## API

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/api/admin/auth/demo-login` | Гостевая сессия `is_demo=true`, read-only |
| GET | `/api/admin/demo/shift-scenes` | Каталог сцен |
| GET | `/api/admin/demo/shift-scene/{id}/state?phase=` | Canned shift/state для фазы pitch |
| GET | `/api/admin/shift/state` | Explore; для `is_demo` — `soften_demo_explore_shift_state` (cap ~12k) |
| POST | `/api/admin/demo/seed` | Seed explore-данных (admin UI; не во время pitch) |

Pitch endpoints требуют `is_demo` session или `APP_DEBUG=true`.

---

## Ключевые файлы

| Слой | Файл |
|------|------|
| Script + phases | [`app/services/demo_shift_scene.py`](../app/services/demo_shift_scene.py) |
| Explore soft cap | [`app/services/demo_shift_presentation.py`](../app/services/demo_shift_presentation.py) |
| Seed после pitch | [`app/services/demo_data.py`](../app/services/demo_data.py) → `_seed_demo_pitch_risks` |
| API | [`app/api/admin/demo.py`](../app/api/admin/demo.py) |
| Autoplay JS | [`app/static/js/admin-app.js`](../app/static/js/admin-app.js) — `startDemoShiftScene`, `stopDemoShiftScene`, `replayDemoPitchScene` |
| UI | [`app/templates/screens/_tab_shift_control.html`](../app/templates/screens/_tab_shift_control.html), [`_login.html`](../app/templates/screens/_login.html) |
| CSS | [`src/css/admin-input.css`](../src/css/admin-input.css) — `rm-demo-scene`, counterfactual, resolve card |

---

## UX-инварианты pitch

- `demoSceneActive` + `rm-demo-scene`: скрыты sidebar, header nav, метрики S2, кнопки focus card, «Готовность N%»
- **Нет** `shift/heartbeat` в demo session (нет spam 401)
- **Нет** `loadShiftState` пока `demoSceneActive` (replay без мигания 90k)
- Звук success tick — тихий, после user gesture (demo-login)

---

## Explore seed (после Esc)

`_seed_demo_pitch_risks` добавляет:

- 3 slow chats (свежие user-сообщения)
- брошенный черновик ~1 200 ₸ + stale drafts
- 2 booking_at_risk
- `daily_org_stats.recovered_kzt = 1200`

`GET /shift/state` в demo session ограничивает отображение: `risk_kzt ≤ 12 000`, `at_risk_count ≤ 3`, S1/S5 → S2, red → amber.

---

## Smoke checklist (ручной)

1. `/admin` → **Посмотреть демо** → 30 сек без кликов
2. Counterfactual banner + auto-action + impact flash видны
3. Resolve → **Осмотреть демо** → очередь рисков без «90k всё красное»
4. **↻ Повторить** → pitch снова, без мигания explore
5. В шапке нет «Готовность 33% 2/6»
6. В Network нет пачки 401 на `/shift/heartbeat` во время pitch

Автотесты: `tests/test_demo_shift_scene.py`, `tests/test_demo_shift_scene_ui.py`, `tests/test_demo_pitch_seed.py`, `tests/test_demo_shift_presentation.py`.

---

## Что не идеально (следующий уровень)

Задачи **не закрыты** — см. [`ROADMAP.md`](ROADMAP.md) § G10.8.2:

- **Landing autoplay** — `/demo` или `?demo=1` без экрана логина (Stripe-style zero friction)
- **Публичная self-demo ссылка** для cold outreach
- **Вариант сценки booking_at_risk** (сейчас только slow_chat)
- **Session hardening на Render** — если 401 после долгого explore (cookie / pooler; см. `SUPABASE_PREFER_TRANSACTION_POOLER`, `DEMO_ORGANIZATION_ID`)

---

## Env (prod demo-login)

| Переменная | Назначение |
|------------|------------|
| `DEMO_ORGANIZATION_ID` | Fast path demo-login без SELECT org |
| `SUPABASE_PREFER_TRANSACTION_POOLER` | `:5432` → `:6543`, меньше EMAXCONNSESSION на Render |

См. [`DEPLOY_RUNBOOK.md`](DEPLOY_RUNBOOK.md), [`CHANGELOG.md`](../CHANGELOG.md) § pool fixes.
