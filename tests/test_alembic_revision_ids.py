from __future__ import annotations

import re
from pathlib import Path


def test_alembic_revision_ids_fit_default_version_column() -> None:
    """Postgres deployments may still have alembic_version.version_num as varchar(32)."""
    too_long: list[tuple[str, str, int]] = []
    for path in Path("alembic/versions").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = re.search(r'^revision:\s*str\s*=\s*["\']([^"\']+)', text, re.MULTILINE)
        if match and len(match.group(1)) > 32:
            too_long.append((path.name, match.group(1), len(match.group(1))))

    assert too_long == []
