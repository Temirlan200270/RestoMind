"""Юнит-тесты E12: байты вектора и косинус."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.menu_embeddings import cosine_similarity, vec_from_bytes, vec_to_bytes


def test_vec_roundtrip_and_cosine_identical() -> None:
    vec = [1.0, 0.0, 0.0, 0.5]
    b = vec_to_bytes(vec)
    back = vec_from_bytes(b)
    assert back is not None
    assert len(back) == 4
    assert abs(back[0] - 1.0) < 1e-6
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_opposite() -> None:
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)
