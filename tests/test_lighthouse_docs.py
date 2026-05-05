"""Наличие инструментов Lighthouse для админки (Phase U6/U7)."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_lighthouse_script_and_package_json():
    script = REPO / "scripts" / "run_admin_lighthouse.mjs"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "lighthouse(" in text
    assert "obtainSessionCookie" in text

    pkg = (REPO / "package.json").read_text(encoding="utf-8")
    assert "lh:admin" in pkg
    assert "lighthouse" in pkg
    assert "playwright" in pkg


def test_lighthouse_docs_readme():
    readme = REPO / "docs" / "ui" / "lighthouse" / "README.md"
    assert readme.is_file()
    assert "npm run lh:admin" in readme.read_text(encoding="utf-8")
