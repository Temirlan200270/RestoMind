"""
Регрессионный тест: баланс <div>...</div> в screen-шаблонах.

Проблема, которую детектирует:
    Лишний или пропущенный </div> не виден линтерам и IDE, но ломает
    layout в браузере. Типичный симптом: блок становится отдельным
    flex-child в lg:flex контейнере и отображается РЯДОМ с контентом
    вместо того, чтобы быть ПОД ним.

Примеры реальных багов:
    - _tab_settings_restaurant.html (2026-05): лишний </div> закрывал
      flex-1 до секции «Платёжные провайдеры». На десктопе платёжный
      блок плавал рядом с формой профиля.

Соглашение: docs/CONVENTIONS.md §8.1
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCREENS_DIR = REPO / "app" / "templates" / "screens"


def _strip_jinja(text: str) -> str:
    """Убираем Jinja2 теги чтобы не мешали парсингу HTML-структуры."""
    # Блочные теги: {% ... %}
    text = re.sub(r"\{%-?\s*.*?-?%\}", " ", text, flags=re.DOTALL)
    # Выражения: {{ ... }}
    text = re.sub(r"\{\{.*?\}\}", '""', text, flags=re.DOTALL)
    return text


def _div_balance(html: str) -> int:
    """Возвращает (кол-во открытий - закрытий).  0 = сбалансировано."""
    opens = len(re.findall(r"<div[\s>]", html))
    closes = html.count("</div>")
    return opens - closes


def _div_depth_at(html: str, search: str) -> int:
    """
    Считает глубину вложенности в точке первого вхождения строки search.
    Если > 0 — элемент вложен в какой-то div.
    Если == 0 — элемент находится снаружи всех divs.
    """
    pos = html.find(search)
    if pos == -1:
        return -999
    fragment = html[:pos]
    return _div_balance(fragment)


# ──────────────────────────────────────────────────────────────────────────────
# Тесты баланса по файлам
# ──────────────────────────────────────────────────────────────────────────────

def test_all_screen_templates_have_balanced_divs():
    """
    Каждый screen-шаблон должен иметь одинаковое количество <div и </div>.
    Дисбаланс означает лишний/пропущенный тег — потенциальный layout-баг.
    """
    failures = []
    for f in sorted(SCREENS_DIR.glob("_tab_*.html")):
        html = _strip_jinja(f.read_text(encoding="utf-8"))
        delta = _div_balance(html)
        if delta != 0:
            opens = len(re.findall(r"<div[\s>]", html))
            closes = html.count("</div>")
            failures.append(
                f"{f.name}: opens={opens}, closes={closes}, delta={delta:+d}"
            )

    assert not failures, (
        "Дисбаланс <div>...</div> в шаблонах:\n"
        + "\n".join(f"  {msg}" for msg in failures)
        + "\n\nПроверьте файлы на лишние или пропущенные </div>. "
        "См. docs/CONVENTIONS.md §8.1"
    )


def test_settings_restaurant_payment_inside_content_div():
    """
    Секция #settings-restaurant-payment должна находиться ВНУТРИ
    flex-1 content div (class='flex-1 min-w-0 space-y-6').

    Регрессия (2026-05): лишний </div> закрывал flex-1 до платёжной секции.
    На десктопе (lg:flex) платёжный блок отображался как отдельная колонка
    рядом с формой профиля вместо того чтобы быть под ней.
    """
    template = (SCREENS_DIR / "_tab_settings_restaurant.html").read_text(encoding="utf-8")
    html = _strip_jinja(template)

    flex1_marker = 'class="flex-1 min-w-0 space-y-6"'
    payment_marker = 'id="settings-restaurant-payment"'
    savebar_marker = "rm-settings-savebar"

    assert flex1_marker in html, "flex-1 content div не найден в шаблоне"
    assert payment_marker in html, "#settings-restaurant-payment не найден в шаблоне"
    assert savebar_marker in html, "rm-settings-savebar не найден в шаблоне"

    # Глубина вложенности в момент открытия платёжной секции
    # Должна быть >= 1 (элемент внутри хотя бы одного div — flex-1)
    depth = _div_depth_at(html, payment_marker)
    assert depth >= 1, (
        f"#settings-restaurant-payment находится ВНЕ flex-1 content div "
        f"(глубина при открытии = {depth}, должна быть >= 1).\n"
        f"Вероятная причина: лишний </div> закрывает flex-1 до платёжной секции.\n"
        f"Проверьте _tab_settings_restaurant.html, ищите лишний </div> "
        f"между концом packaging-секции и #settings-restaurant-payment."
    )

    # Save bar тоже должен быть внутри flex-1
    depth_savebar = _div_depth_at(html, savebar_marker)
    assert depth_savebar >= 1, (
        f"rm-settings-savebar находится ВНЕ flex-1 content div "
        f"(глубина = {depth_savebar}). Тот же баг с преждевременным закрытием."
    )


def test_settings_restaurant_outer_container_closes_last():
    """
    Внешний lg:flex контейнер (_tab_settings_restaurant.html, строка 2)
    должен быть закрыт ПОСЛЕ всех дочерних секций.
    Проверяем что в конце файла правильное количество закрывающих тегов.
    """
    template = (SCREENS_DIR / "_tab_settings_restaurant.html").read_text(encoding="utf-8")
    html = _strip_jinja(template)

    # Файл должен быть полностью сбалансирован (тест выше уже проверяет это,
    # но здесь добавляем явный assert с понятным сообщением)
    delta = _div_balance(html)
    assert delta == 0, (
        f"Внешний контейнер не закрыт (delta={delta:+d}). "
        f"Убедитесь что в конце файла есть </div> для каждого открытого <div>."
    )
