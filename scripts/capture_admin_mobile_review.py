"""
Съёмка mobile-review PNG для админки (docs/ui/mobile-review/).

Запуск из корня репозитория:
  pip install playwright
  python -m playwright install chromium
  python scripts/capture_admin_mobile_review.py

Поднимает временный uvicorn на MOBILE_REVIEW_PORT (по умолчанию 9878) с отдельным SQLite-файлом,
входит через «Попробовать демо», обходит hash-навигацию и сохраняет скрины в docs/ui/mobile-review/.
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
OUT_DIR = REPO_ROOT / "docs" / "ui" / "mobile-review"
DEFAULT_PORT = 9878


VIEWPORTS: list[tuple[int, int]] = [
    (320, 640),
    (390, 844),
    (412, 915),
]

# Имя файла → hash (без ведущего #)
BASE_SHOTS: list[tuple[str, str]] = [
    ("dashboard", "dashboard"),
    ("orders", "orders"),
    ("chats", "chats"),
    ("menu", "menu"),
    ("settings_restaurant", "settings/restaurant"),
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
        from playwright.sync_api import TimeoutError as PwTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Установите: pip install playwright && python -m playwright install chromium", file=sys.stderr)
        return 1

    port = int(os.environ.get("MOBILE_REVIEW_PORT", str(DEFAULT_PORT)))
    base = f"http://127.0.0.1:{port}"

    fd, db_path = tempfile.mkstemp(suffix="_mobile_review.db")
    os.close(fd)
    try:
        db_url = "sqlite+aiosqlite:///" + Path(db_path).as_posix()
        env = os.environ.copy()
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

                for w, h in VIEWPORTS:
                    context = browser.new_context(viewport={"width": w, "height": h})
                    page = context.new_page()

                    page.goto(f"{base}/admin", wait_until="domcontentloaded", timeout=120_000)
                    page.get_by_role("button", name="Попробовать демо").click(timeout=60_000)
                    page.wait_for_timeout(4000)
                    page.locator("nav.admin-sidebar-scroll").wait_for(state="visible", timeout=120_000)

                    prefix = f"{w}_"
                    for name, frag in BASE_SHOTS:
                        fname = f"{prefix}{name}.png"
                        page.goto(f"{base}/admin#{frag}", wait_until="domcontentloaded", timeout=120_000)
                        page.wait_for_timeout(2500)
                        page.screenshot(path=str(OUT_DIR / fname), full_page=False)
                        print("OK", fname)

                    # after_...: пару рабочих сценариев на 320px (как в README)
                    if w == 320:
                        # Orders → открыть фильтры (если кнопка есть)
                        page.goto(f"{base}/admin#orders", wait_until="domcontentloaded", timeout=120_000)
                        page.wait_for_timeout(2500)
                        page.screenshot(path=str(OUT_DIR / "after_320_orders.png"), full_page=False)
                        try:
                            # На разных фазах UI кнопка называлась «Фильтры» или имела aria-label.
                            btn = page.get_by_role("button", name="Фильтры")
                            btn.click(timeout=1500)
                            page.wait_for_timeout(1200)
                            page.screenshot(path=str(OUT_DIR / "after_320_orders_filters.png"), full_page=False)
                            print("OK", "after_320_orders_filters.png")
                        except (PwTimeoutError, Exception):
                            # Не фейлим прогон: главное — базовые скрины.
                            pass

                        # Chats → список и открытый чат
                        page.goto(f"{base}/admin#chats", wait_until="domcontentloaded", timeout=120_000)
                        page.wait_for_timeout(2500)
                        page.screenshot(path=str(OUT_DIR / "after_320_chats_list.png"), full_page=False)
                        print("OK", "after_320_chats_list.png")
                        try:
                            # Попробуем кликнуть по первому диалогу в списке.
                            # Селектор устойчив к изменениям data-атрибутов: берём первую строку из scroll list.
                            page.locator(
                                ".ds-chat-shell-list .flex-1.min-h-0.overflow-y-auto button.w-full.text-left",
                            ).first.click(timeout=4000)
                            page.wait_for_timeout(1500)
                            page.screenshot(path=str(OUT_DIR / "after_320_chat_open.png"), full_page=False)
                            print("OK", "after_320_chat_open.png")
                        except (PwTimeoutError, Exception):
                            pass

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

