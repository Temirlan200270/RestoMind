"""
Съёмка baseline PNG для Phase U0 (только /admin).

Запуск из корня репозитория:
  pip install playwright
  python -m playwright install chromium
  python scripts/capture_admin_u0_baseline.py

Поднимает временный uvicorn на U0_BASELINE_PORT (по умолчанию 9877) с отдельным SQLite-файлом,
входит через «Попробовать демо», обходит hash-навигацию и сохраняет скрины в docs/ui/baseline/.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs" / "ui" / "baseline"
DEFAULT_PORT = 9877

# (filename, hash без ведущего #) — порядок как в docs/UI_REDESIGN_PLAN (простые → тяжелее).
SHOTS: list[tuple[str, str]] = [
    ("admin_dashboard.png", "dashboard"),
    ("admin_settings_bot_test.png", "settings/bot_test"),
    ("admin_settings_technical.png", "settings/technical"),
    ("admin_settings_health.png", "settings/health"),
    ("admin_settings_team.png", "settings/team"),
    ("admin_settings_smart_sales.png", "settings/smart_sales"),
    ("admin_settings_connections.png", "settings/connections"),
    ("admin_settings_branding.png", "settings/branding"),
    ("admin_settings_restaurant.png", "settings/restaurant"),
    ("admin_errors.png", "errors"),
    ("admin_incidents.png", "incidents"),
    ("admin_operator_queue.png", "operator_queue"),
    ("admin_bookings.png", "bookings"),
    ("admin_chats.png", "chats"),
    ("admin_menu.png", "menu"),
    ("admin_stoplist.png", "stoplist"),
    ("admin_orders.png", "orders"),
    ("admin_analytics.png", "analytics"),
    ("admin_ai_value.png", "ai_value"),
]


def _wait_http(url: str, timeout_s: float = 300.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError) as e:
            last_err = str(e)
        time.sleep(0.4)
    raise RuntimeError(f"Сервер не поднялся за {timeout_s}s: {url} ({last_err})")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Установите: pip install playwright && python -m playwright install chromium", file=sys.stderr)
        return 1

    port = int(os.environ.get("U0_BASELINE_PORT", str(DEFAULT_PORT)))
    base = f"http://127.0.0.1:{port}"

    fd, db_path = tempfile.mkstemp(suffix="_u0_baseline.db")
    os.close(fd)
    try:
        db_url = "sqlite+aiosqlite:///" + Path(db_path).as_posix()
        env = os.environ.copy()
        # True: иначе при непустом DATABASE_URL срабатывает prod-like и требуется SESSION_SECRET.
        env["APP_DEBUG"] = "true"
        env["REDIS_MEMORY_ONLY"] = "1"
        env["DATABASE_URL"] = db_url
        env["APP_ENV"] = "development"

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_http(f"{base}/admin")

            OUT_DIR.mkdir(parents=True, exist_ok=True)

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()

                page.goto(f"{base}/admin", wait_until="domcontentloaded", timeout=120_000)
                page.get_by_role("button", name="Попробовать демо").click(timeout=60_000)
                page.wait_for_timeout(4000)
                # После демо-логина виден сайдбар (см. admin.html — nav.admin-sidebar-scroll).
                page.locator("nav.admin-sidebar-scroll").wait_for(state="visible", timeout=120_000)

                for fname, frag in SHOTS:
                    page.goto(f"{base}/admin#{frag}", wait_until="domcontentloaded", timeout=120_000)
                    page.wait_for_timeout(2800)
                    target = OUT_DIR / fname
                    page.screenshot(path=str(target), full_page=False)
                    print("OK", fname)

                context.close()
                browser.close()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
