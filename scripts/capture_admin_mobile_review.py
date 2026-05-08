"""
Съёмка mobile-review PNG для админки (docs/ui/mobile-review/).

Запуск из корня репозитория:
  pip install playwright
  python -m playwright install chromium
  python scripts/capture_admin_mobile_review.py

По умолчанию пытается снять скрины с актуального URL (MOBILE_REVIEW_BASE_URL),
а если это не удалось — поднимает временный uvicorn на MOBILE_REVIEW_PORT (по умолчанию 9878)
с отдельным SQLite-файлом, входит через «Попробовать демо», обходит hash-навигацию
и сохраняет скрины в docs/ui/mobile-review/.
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
    Снять mobile-review с base URL.
    Если ADMIN_PASSWORD задан — логин через username/password. Иначе — demo-login (если allow_demo_login=True).
    """
    from playwright.sync_api import TimeoutError as PwTimeoutError
    from playwright.sync_api import sync_playwright

    _load_root_env()
    admin_user = (os.environ.get("ADMIN_USERNAME") or "admin").strip()
    admin_pass = (os.environ.get("ADMIN_PASSWORD") or "").strip()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Чтобы mobile-review не “смешивался” с предыдущим прогоном: удаляем только файлы,
    # которые этот скрипт сам генерирует (все viewport+BASE_SHOTS и after_320_*).
    for w, _h in VIEWPORTS:
        prefix = f"{w}_"
        for name, _frag in BASE_SHOTS:
            try:
                (OUT_DIR / f"{prefix}{name}.png").unlink(missing_ok=True)  # py3.8+ shim below
            except TypeError:
                # Python <3.8: no missing_ok
                p = OUT_DIR / f"{prefix}{name}.png"
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass
            except OSError:
                pass
    for fname in ["after_320_orders.png", "after_320_orders_filters.png", "after_320_chats_list.png", "after_320_chat_open.png"]:
        try:
            (OUT_DIR / fname).unlink(missing_ok=True)
        except TypeError:
            p = OUT_DIR / fname
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        except OSError:
            pass

    def _dismiss_modal_if_any(page) -> None:
        # Иногда после логина всплывает uiConfirm «Готово…» и закрывает интерфейс на скринах.
        try:
            btn = page.get_by_role("button", name="Понятно")
            if btn.is_visible(timeout=1200):
                btn.click(timeout=2000)
                page.wait_for_timeout(300)
        except Exception:
            return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for w, h in VIEWPORTS:
            context = browser.new_context(viewport={"width": w, "height": h})
            page = context.new_page()

            page.goto(f"{base}/admin", wait_until="domcontentloaded", timeout=120_000)
            sidebar = page.locator("nav.admin-sidebar-scroll")
            if not sidebar.is_visible(timeout=2000):
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

            prefix = f"{w}_"
            for name, frag in BASE_SHOTS:
                fname = f"{prefix}{name}.png"
                page.goto(f"{base}/admin#{frag}", wait_until="domcontentloaded", timeout=120_000)
                page.wait_for_timeout(2500)
                _dismiss_modal_if_any(page)
                page.screenshot(path=str(OUT_DIR / fname), full_page=False)
                print("OK", fname)

            # after_...: пару рабочих сценариев на 320px (как в README)
            if w == 320:
                page.goto(f"{base}/admin#orders", wait_until="domcontentloaded", timeout=120_000)
                page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT_DIR / "after_320_orders.png"), full_page=False)
                print("OK", "after_320_orders.png")
                try:
                    btn = page.get_by_role("button", name="Фильтры")
                    btn.click(timeout=1500)
                    page.wait_for_timeout(1200)
                    page.screenshot(path=str(OUT_DIR / "after_320_orders_filters.png"), full_page=False)
                    print("OK", "after_320_orders_filters.png")
                except (PwTimeoutError, Exception):
                    pass

                page.goto(f"{base}/admin#chats", wait_until="domcontentloaded", timeout=120_000)
                page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT_DIR / "after_320_chats_list.png"), full_page=False)
                print("OK", "after_320_chats_list.png")
                try:
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


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Установите: pip install playwright && python -m playwright install chromium", file=sys.stderr)
        return 1

    port = int(os.environ.get("MOBILE_REVIEW_PORT", str(DEFAULT_PORT)))
    remote_base = (os.environ.get("MOBILE_REVIEW_BASE_URL") or "https://restomind.onrender.com").strip().rstrip("/")

    fd, db_path = tempfile.mkstemp(suffix="_mobile_review.db")
    os.close(fd)
    try:
        # 1) Пытаемся снять с актуального сайта (prod/stage).
        try:
            _wait_http(f"{remote_base}/admin", timeout_s=45.0)
            _capture(remote_base, allow_demo_login=False)
            return 0
        except Exception as e:
            print(f"[mobile-review] Не удалось снять с {remote_base}: {e}\n[mobile-review] Падаем на локальный режим…")

        # 2) Фоллбек: локальный uvicorn + демо-логин (как раньше).
        base = f"http://127.0.0.1:{port}"
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

