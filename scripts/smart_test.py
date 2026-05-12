"""
Smart test selector: запускает только тесты, относящиеся к изменённым файлам.

Использование:
  python scripts/smart_test.py           # auto-detect changed files
  python scripts/smart_test.py --all     # запустить всё
  python scripts/smart_test.py --dry     # показать команду, не запускать
  python scripts/smart_test.py --base main  # сравнить с другой веткой

В CI: python scripts/smart_test.py (сравнивает HEAD с origin/main)
Локально: сравнивает рабочее дерево + staged с HEAD
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ─── Маппинг: паттерн пути → тест-файлы ────────────────────────────────────
#
# Порядок важен: первое совпадение определяет группу.
# Если файл попадает в несколько групп — все они включаются.
#
# Ключи — glob-паттерны для fnmatch (case-insensitive).
# Значения — список тест-файлов или -k выражений.

GROUPS: dict[str, list[str]] = {
    # ── UI / Шаблоны / CSS / JS ─────────────────────────────────────────────
    "app/templates/": [
        "tests/test_template_div_balance.py",
        "tests/test_ui_u6_a11y.py",
        "tests/test_ui_u45.py",
        "tests/test_admin_tab_surface_audit.py",
        "tests/test_lighthouse_docs.py",
    ],
    "app/static/": [
        "tests/test_ui_u6_a11y.py",
        "tests/test_template_div_balance.py",
    ],
    "src/css/": [
        "tests/test_ui_u6_a11y.py",
        "tests/test_template_div_balance.py",
    ],

    # ── Admin API ────────────────────────────────────────────────────────────
    "app/api/admin/": [
        "tests/test_admin_login_regression.py",
        "tests/test_admin_customers.py",
        "tests/test_admin_bookings_routes.py",
        "tests/test_admin_branding.py",
        "tests/test_admin_incidents.py",
        "tests/test_admin_readiness.py",
        "tests/test_admin_analytics.py",
        "tests/test_admin_menu_bulk_stoplist.py",
        "tests/test_admin_failed_tasks_retry.py",
        "tests/test_admin_multitenant_ws_resend.py",
        "tests/test_admin_operator_outbound.py",
        "tests/test_admin_orders_p15.py",
        "tests/test_admin_system_task_queue_health.py",
        "tests/test_ui_u45.py",
        "tests/test_select_org.py",
        "tests/test_superadmin_onboarding.py",
    ],

    # ── Payments ─────────────────────────────────────────────────────────────
    "app/api/payment_webhook.py": [
        "tests/test_payment_webhook.py",
        "tests/test_payment_webhook_audit.py",
        "tests/test_payment_webhook_dispatcher.py",
    ],
    "app/services/payment": [
        "tests/test_payment_adapters_cloudpayments.py",
        "tests/test_payment_adapters_generic_hmac.py",
        "tests/test_payment_autoprint_iiko.py",
        "tests/test_payment_notify.py",
        "tests/test_payment_webhook.py",
        "tests/test_payment_webhook_audit.py",
        "tests/test_payment_webhook_dispatcher.py",
    ],

    # ── AI / LLM ─────────────────────────────────────────────────────────────
    "app/services/ai_brain.py": [
        "tests/test_ai_brain.py",
        "tests/test_ai_provider_resolution.py",
        "tests/test_context_engine.py",
        "tests/test_pipeline_timing.py",
    ],
    "app/services/ai_engine/": [
        "tests/test_ai_brain.py",
        "tests/test_ai_schemas.py",
        "tests/test_gemini_provider.py",
        "tests/test_ai_provider_resolution.py",
    ],
    "app/services/context_engine.py": [
        "tests/test_context_engine.py",
        "tests/test_restaurant_context_cache.py",
    ],
    "app/schemas/": [
        "tests/test_ai_schemas.py",
        "tests/test_order_logic.py",
    ],

    # ── Orders / Intent ──────────────────────────────────────────────────────
    "app/services/intent_router.py": [
        "tests/test_order_logic.py",
        "tests/test_booking_preorder.py",
        "tests/test_intent_phase18.py",
        "tests/test_atomic_merge.py",
        "tests/test_action_id_dedup.py",
        "tests/test_pricing.py",
        "tests/test_order_confidence.py",
    ],
    "app/services/order_logic.py": [
        "tests/test_order_logic.py",
        "tests/test_pricing.py",
        "tests/test_atomic_merge.py",
        "tests/test_admin_menu_bulk_stoplist.py",
    ],

    # ── Dialogs / State ──────────────────────────────────────────────────────
    "app/services/dialog_mgr.py": [
        "tests/test_conversation_state.py",
        "tests/test_dialog_state_events.py",
        "tests/test_booking_preorder.py",
    ],
    "app/services/conversation_state.py": [
        "tests/test_conversation_state.py",
        "tests/test_dialog_state_events.py",
    ],
    "app/services/trace_context.py": [
        "tests/test_conversation_state.py",
        "tests/test_dialog_state_events.py",
    ],
    "app/api/webhooks.py": [
        "tests/test_booking_preorder.py",
        "tests/test_conversation_state.py",
        "tests/test_pipeline_timing.py",
        "tests/test_restaurant_context_cache.py",
    ],

    # ── Sales / Analytics ────────────────────────────────────────────────────
    "app/services/sales_strategy": [
        "tests/test_sales_strategy.py",
        "tests/test_sales_strategy_engine.py",
        "tests/test_tag_pairing_sales.py",
        "tests/test_recommendation_gastro_hint.py",
    ],
    "app/services/intelligence": [
        "tests/test_intelligence_analytics.py",
        "tests/test_intelligence_operations.py",
        "tests/test_ai_value_metrics.py",
    ],

    # ── Menu / iiko ──────────────────────────────────────────────────────────
    "app/services/menu_sync.py": [
        "tests/test_menu_sync_db.py",
        "tests/test_menu_sync_price.py",
        "tests/test_stop_list_terminal_group.py",
    ],
    "app/api/iiko_webhook.py": [
        "tests/test_menu_sync_db.py",
    ],
    "app/services/iiko_sync_tasks.py": [
        "tests/test_menu_sync_db.py",
    ],

    # ── Knowledge / RAG ──────────────────────────────────────────────────────
    "app/services/knowledge": [
        "tests/test_knowledge_context_split.py",
    ],

    # ── Multi-tenant ─────────────────────────────────────────────────────────
    "app/db/models.py": [
        "tests/test_multitenant_isolation.py",
        "tests/test_admin_login_regression.py",
    ],
    "alembic/": [
        "tests/test_multitenant_isolation.py",
        "tests/test_admin_login_regression.py",
    ],

    # ── Billing ──────────────────────────────────────────────────────────────
    "app/services/billing": [
        "tests/test_billing_e23.py",
        "tests/test_billing_suspended_contract.py",
    ],

    # ── Worker / ARQ ─────────────────────────────────────────────────────────
    "app/worker.py": [
        "tests/test_worker_arq_config.py",
        "tests/test_task_queue_arq_only.py",
        "tests/test_task_queue_logging.py",
    ],
    "app/services/task_queue.py": [
        "tests/test_task_queue_arq_only.py",
        "tests/test_task_queue_logging.py",
    ],

    # ── WhatsApp / Integrations ──────────────────────────────────────────────
    "app/integrations/whatsapp.py": [
        "tests/test_chat_delivery.py",
        "tests/test_admin_operator_outbound.py",
        "tests/test_whatsapp_markdown_sanitizer.py",
    ],
}

# Файлы, при изменении которых нужен ПОЛНЫЙ прогон
FULL_SUITE_TRIGGERS = [
    "app/main.py",
    "app/core/config.py",
    "requirements.txt",
    "app/db/session.py",
    "tests/conftest.py",
    ".github/workflows/",
]

# Тесты, которые запускаются ВСЕГДА (smoke)
SMOKE_TESTS = [
    "tests/test_admin_login_regression.py",
]


def get_changed_files(base: str | None = None) -> list[str]:
    """Возвращает список изменённых файлов."""
    files: list[str] = []

    # CI: сравниваем с origin/main или указанной базой
    ref = base or "origin/main"
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", f"{ref}...HEAD"],
            capture_output=True, text=True, check=True, cwd=ROOT,
        )
        files = [f.strip() for f in r.stdout.strip().splitlines() if f.strip()]
    except subprocess.CalledProcessError:
        pass

    # Локально: добавляем unstaged + staged изменения
    if not files or base is None:
        for git_args in (
            ["git", "diff", "--name-only"],          # unstaged
            ["git", "diff", "--name-only", "--cached"],  # staged
        ):
            try:
                r = subprocess.run(
                    git_args, capture_output=True, text=True, check=True, cwd=ROOT,
                )
                files += [f.strip() for f in r.stdout.strip().splitlines() if f.strip()]
            except subprocess.CalledProcessError:
                pass

    return sorted(set(files))


def select_tests(changed: list[str]) -> tuple[list[str], bool]:
    """
    Возвращает (список_тест_файлов, нужен_полный_прогон).
    """
    # Проверяем trigg full suite
    for f in changed:
        for trigger in FULL_SUITE_TRIGGERS:
            if f.startswith(trigger) or f == trigger:
                return [], True

    selected: set[str] = set(SMOKE_TESTS)

    for changed_file in changed:
        for pattern, tests in GROUPS.items():
            if changed_file.startswith(pattern) or changed_file == pattern:
                selected.update(tests)

    # Оставляем только существующие файлы
    existing = [t for t in sorted(selected) if (ROOT / t).exists()]
    return existing, False


def run_tests(test_files: list[str], full: bool, dry: bool = False) -> int:
    if full:
        cmd = [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short", "-m", "not regression"]
        label = "[full] Full test suite"
    elif test_files:
        cmd = [sys.executable, "-m", "pytest", *test_files, "-q", "--tb=short"]
        label = f"[smart] {len(test_files)} test file(s)"
    else:
        print("No changes detected — nothing to run.")
        return 0

    print(f"\n{label}")
    print("  " + " ".join(cmd[2:]), "\n")

    if dry:
        return 0

    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart pytest selector")
    parser.add_argument("--all", action="store_true", help="Запустить всю suite")
    parser.add_argument("--dry", action="store_true", help="Показать команду без запуска")
    parser.add_argument("--base", default=None, help="Git ref для сравнения (default: origin/main)")
    args = parser.parse_args()

    if args.all:
        sys.exit(run_tests([], full=True, dry=args.dry))

    changed = get_changed_files(base=args.base)

    if changed:
        print(f"Changed files: {len(changed)}")
        for f in changed[:10]:
            print(f"  {f}")
        if len(changed) > 10:
            print(f"  ... and {len(changed) - 10} more")
    else:
        print("No changed files detected — running smoke tests")

    test_files, full = select_tests(changed)
    sys.exit(run_tests(test_files, full=full, dry=args.dry))


if __name__ == "__main__":
    main()
