"""
Автонастройка sys.path для запуска тестов как скриптов.

Когда вы запускаете файл напрямую:

    cd tests
    python test_ai_brain.py

Python добавляет текущую директорию (tests/) в sys.path, но НЕ корень репозитория.
Из-за этого импорты вида `from app...` падают.

`sitecustomize.py` автоматически импортируется интерпретатором при старте, если он
находится в sys.path. Поэтому этот файл гарантированно подхватывается при запуске
скриптов из папки tests/ и добавляет корень проекта в sys.path.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_s = str(ROOT)
if root_s not in sys.path:
    sys.path.insert(0, root_s)

