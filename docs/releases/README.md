# Release notes — политика журналирования

RestoMind ведёт **трёхуровневую** историю изменений. Цель — не раздувать корневой `CHANGELOG.md` (агенты и люди читают его часто), но не терять детали для аудита и онбординга.

## Три слоя

| Слой | Файл | Когда писать | Формат |
|------|------|--------------|--------|
| **Задачи / статус** | [`docs/ROADMAP.md`](../ROADMAP.md) | Всегда при работе над фичей | `[ ]` / `[x]`, приоритеты P0–P5 |
| **Релизы (кратко)** | [`CHANGELOG.md`](../../CHANGELOG.md) | Закрыли эпик или существенную поставку | 5–15 буллетов **на релиз**, без перечисления каждого файла |
| **Детали эпика** | `docs/releases/*.md` | Нужен аудит: миграции, тесты, файлы | Свободный, можно длинно |

**Не дублировать** ROADMAP в CHANGELOG: ROADMAP — «что делаем», CHANGELOG — «что отгрузили».

## Именование релизов

Используем **этап продукта + CalVer**, не хронологию коммитов:

```text
## [2026.06 — Owner Intelligence OS] — 2026-06-04
```

- Первая часть — **смысл этапа** (эпик из ROADMAP / OS Transition Plan).
- Дата — когда этап **закрыт для пилота** (не каждый merge).
- SemVer (`1.2.3`) опционален; для SaaS/OS CalVer + slug понятнее команде.

## Workflow для агента

1. **В процессе работы** — дописывать только в `CHANGELOG.md` → `## [Unreleased]` (1 блок на эпик, 3–10 строк).
2. **Закрыли эпик** — `[x]` в ROADMAP; в CHANGELOG:
   - сжать `[Unreleased]` в новую секцию `## [YYYY.MM — Epic name]`;
   - при необходимости вынести длинный список в `docs/releases/YYYY.MM-slug.md`.
3. **Не редактировать** уже опубликованные release-секции (как и раньше).
4. **Не копировать** в CHANGELOG простыни из diff — для этого архив.

## Файлы в этой папке

| Файл | Содержание |
|------|------------|
| [`archive-detailed.md`](archive-detailed.md) | Полный дамп старого CHANGELOG (до реструктуризации 2026-05-26) |
| [`2026.06-owner-intelligence.md`](2026.06-owner-intelligence.md) | OI, Copilot, Telegram, Performance Pack |
| [`2026.05-execution-os.md`](2026.05-execution-os.md) | G10 Money Core, Focus-Driven Admin, Final Mile |
| [`2026.05-os-foundation.md`](2026.05-os-foundation.md) | OS Phases 1–5, Event System, DE, Snapshot |
| [`2026.04-platform-admin.md`](2026.04-platform-admin.md) | Admin UX U1–U7, multitenancy, платформенные фичи |
| [`legacy-product-baseline.md`](legacy-product-baseline.md) | Ранний срез «что умеет продукт» (до OS) |
| [`2026-misc-unclassified.md`](2026-misc-unclassified.md) | Хвост записей, не попавших в срезы |

## Связь с OS Transition Plan

Крупные релизы в CHANGELOG должны **мапиться на фазы** [`docs/OS_TRANSITION_PLAN.md`](../OS_TRANSITION_PLAN.md) (Phase 1–6, Final Mile, UI Layer). Если эпик не про фазу OS — используйте имя из ROADMAP (например «Operator reliability»).

## Альтернативы (если вырастем ещё)

- **GitHub Releases** — для внешних клиентов; CHANGELOG остаётся source of truth в репо.
- **Автоген из conventional commits** — только если появится дисциплина коммитов; сейчас ручной журнал точнее для solo/AI workflow.
- **Удалить `archive-detailed.md`** — когда milestone-файлы покрывают 100% и никто не grep'ает старый формат (пока оставляем).
