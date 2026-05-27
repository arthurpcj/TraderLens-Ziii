"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_xml_bytes() -> bytes:
    """Real (sanitized) Flex XML from spike 001 — 33 EXECUTION trades."""
    return (FIXTURES / "sample_flex.xml").read_bytes()
