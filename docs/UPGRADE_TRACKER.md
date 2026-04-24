# RestoMind upgrade tracker

Статусы: `done` | `in_progress` | `partial` | `blocked`

## P0 — UI/UX + операционный контур
| Подэтап | Статус | Примечание |
|--------|--------|------------|
| Вкладки настроек: `technical` + отдельная `bot_test`, без дубля под одним табом | done | `_settings_tabs.html`, `admin.html`, `admin-app.js`, legacy `#test` → `bot_test` |
| Мобильная модалка заказа (`dvh`, нижний sheet, скролл, footer) | done | `admin.html` |
| Sales Insight / оплата: скролл и сворачивание оплаты на мобильном | partial | `details` + `max-h` на таймлайне |
| Дашборд: контраст карточки ИИ + явная связь с аналитикой | done | Светлая карточка + подпись |
| WhatsApp: ошибка переотправки видна оператору | done | `resendFailedChatMessage` → `showUiAlert` |
| iiko: подсказка при ошибке маршрута/терминала | done | Текст под блоком ошибки в модалке заказа |
| Telegram: крупный заказ с предоплатой | done | `send_prepayment_large_order_alert` при ветке предоплаты |

## P1 — Структурное меню + decision layer
| Подэтап | Статус | Примечание |
|--------|--------|------------|
| Поля `MenuItem` + миграция + SQLite DDL | done | `models.py`, `alembic`, `main.py` |
| Админка: редактирование профиля блюда | done | PATCH/POST + форма меню |
| Промпт: гости, диета, порции в меню-контексте | done | `prompts.py`, `build_menu_context` |
| `AIBrainResponse`: гости + ограничения | done | `ai_schemas.py`, `order_meta`, iiko `comment` |
| Серверная подсказка по числу гостей / порционности | done | `apply_guest_meal_guidance_reply` |

## P2 — Продукт и масштаб
| Подэтап | Статус | Примечание |
|--------|--------|------------|
| Онбординг + типы базы знаний в UI | partial | Баннер + `knowledge_kind` в форме |
| White-label в сайдбаре (название филиала) | partial | Заголовок из `orgProfile` |
| Аналитика: заказы по часу UTC | done | `GET /analytics` + блок в UI |
| Учёт токенов ИИ | partial | Логирование usage в `openai_p.py` |
| Vite | см. `VITE_DECISION.md` | `done` (решение зафиксировано) |

## Остаточные риски
- Полная синхронизация меню из iiko по-прежнему перезаписывает только номенклатурные поля; кастомные `portion_kind` / аллергены сохраняются на существующих строках, но при удалении+полном импорте их нужно заново заполнить.
- Счётчик токенов пока только в логах; отдельная метрика/БД — при росте нагрузки.
