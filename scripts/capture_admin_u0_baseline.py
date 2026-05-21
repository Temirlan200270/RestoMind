"""
Съёмка baseline PNG для Phase U0 (только /admin).

Запуск из корня репозитория:
  pip install playwright
  python -m playwright install chromium
  python scripts/capture_admin_u0_baseline.py

По умолчанию пытается снять скрины с актуального URL (U0_BASELINE_BASE_URL),
а если это не удалось — поднимает временный uvicorn на U0_BASELINE_PORT (по умолчанию 9877)
с отдельным SQLite-файлом, входит через «Попробовать демо», обходит hash-навигацию
и сохраняет скрины в docs/ui/baseline/.
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
    # P1.5.0: legacy hash → новые вкладки/подтабы (имена файлов сохраняем для docs/ui/baseline)
    ("admin_errors.png", "inbox?tab=clients"),
    ("admin_incidents.png", "inbox?tab=system"),
    ("admin_operator_queue.png", "inbox?tab=clients"),
    ("admin_bookings.png", "bookings"),
    ("admin_chats.png", "chats"),
    ("admin_menu.png", "menu"),
    ("admin_stoplist.png", "stoplist"),
    ("admin_orders.png", "orders"),
    ("admin_analytics.png", "dashboard?tab=analytics"),
    ("admin_ai_value.png", "ai_center?tab=value"),
    ("admin_ai_center_final_mile.png", "ai_center?tab=final_mile"),
    ("admin_ai_center_guestcare.png", "ai_center?tab=guestcare"),
    ("admin_shift_control.png", "shift"),
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


def _load_root_env() -> None:
    """Подхват ADMIN_* из корневого `.env`, если переменные не заданы в окружении."""
    p = REPO_ROOT / ".env"
    if not p.exists():
        return
    try:
        for raw in p.read_text("utf-8").splitlines():
            t = raw.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            key, val = t.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key == "ADMIN_USERNAME" and not os.environ.get("ADMIN_USERNAME"):
                os.environ["ADMIN_USERNAME"] = val
            if key == "ADMIN_PASSWORD" and not os.environ.get("ADMIN_PASSWORD"):
                os.environ["ADMIN_PASSWORD"] = val
    except OSError:
        return


def _capture(base: str, *, allow_demo_login: bool) -> None:
    """
    Снять все SHOTS с base URL.
    Если ADMIN_PASSWORD задан — логин через username/password. Иначе — demo-login (если allow_demo_login=True).
    """
    from playwright.sync_api import sync_playwright

    _load_root_env()
    admin_user = (os.environ.get("ADMIN_USERNAME") or "admin").strip()
    admin_pass = (os.environ.get("ADMIN_PASSWORD") or "").strip()

    def _dismiss_modal_if_any(page) -> None:
        # Иногда после логина всплывает uiConfirm «Готово…» и закрывает интерфейс на скринах.
        # Для baseline нам важно видеть UI, поэтому мягко закрываем, если модалка есть.
        try:
            btn = page.get_by_role("button", name="Понятно")
            if btn.is_visible(timeout=1200):
                btn.click(timeout=2000)
                page.wait_for_timeout(300)
        except Exception:
            return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page.goto(f"{base}/admin", wait_until="domcontentloaded", timeout=120_000)

        sidebar = page.locator("nav.admin-sidebar-scroll")
        if not sidebar.is_visible(timeout=2000):
            # Логин обязателен (prod/stage) или мы ещё не в демо (local).
            if admin_pass:
                page.locator('input[autocomplete="username"]').fill(admin_user, timeout=60_000)
                page.locator('input[autocomplete="current-password"]').fill(admin_pass, timeout=60_000)
                page.get_by_role("button", name="Войти").click(timeout=60_000)
            else:
                if not allow_demo_login:
                    raise RuntimeError(
                        "Нет ADMIN_PASSWORD для логина, а demo-login запрещён для текущей базы. "
                        "Задайте ADMIN_PASSWORD (и при желании ADMIN_USERNAME) в env или .env.",
                    )
                page.get_by_role("button", name="Попробовать демо").click(timeout=60_000)

        page.wait_for_timeout(3500)
        sidebar.wait_for(state="visible", timeout=120_000)
        _dismiss_modal_if_any(page)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        # Чтобы baseline не “смешивался” с предыдущими прогоном: удаляем только файлы,
        # которые этот скрипт сам генерирует (имена из SHOTS).
        for fname, _frag in SHOTS:
            target = OUT_DIR / fname
            try:
                if target.exists():
                    target.unlink()
            except OSError:
                # Не блокируем прогон из-за Windows file lock/AV; просто перезапишем screenshot ниже.
                pass

        for fname, frag in SHOTS:
            page.goto(f"{base}/admin#{frag}", wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_timeout(2800)
            _dismiss_modal_if_any(page)
            target = OUT_DIR / fname
            page.screenshot(path=str(target), full_page=False)
            print("OK", fname)

        context.close()
        browser.close()


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Установите: pip install playwright && python -m playwright install chromium", file=sys.stderr)
        return 1

    port = int(os.environ.get("U0_BASELINE_PORT", str(DEFAULT_PORT)))
    remote_base = (os.environ.get("U0_BASELINE_BASE_URL") or "https://restomind.onrender.com").strip().rstrip("/")

    fd, db_path = tempfile.mkstemp(suffix="_u0_baseline.db")
    os.close(fd)
    try:
        # 1) Пытаемся снять с актуального сайта (prod/stage).
        try:
            _wait_http(f"{remote_base}/admin", timeout_s=45.0)
            _capture(remote_base, allow_demo_login=False)
            return 0
        except Exception as e:
            print(f"[baseline] Не удалось снять с {remote_base}: {e}\n[baseline] Падаем на локальный режим…")

        # 2) Фоллбек: локальный uvicorn + демо-логин (как раньше).
        base = f"http://127.0.0.1:{port}"
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
            _capture(base, allow_demo_login=True)
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
