"""Unit tests for utils.driver_code.generate_driver_code."""

from __future__ import annotations

import re

try:
    from utils.driver_code import generate_driver_code
except ImportError:
    from backend.utils.driver_code import generate_driver_code  # type: ignore[no-redef]

_PATTERN = re.compile(r"^DRV-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$")


def test_format_and_alphabet():
    for _ in range(200):
        code = generate_driver_code()
        assert _PATTERN.match(code), code
        # the most read-aloud-ambiguous glyphs (0/1/O/I) must never appear
        assert not set(code) & set("01OI")


def test_codes_are_random():
    # 1000 draws from ~1e9 space should be unique (collision astronomically rare)
    codes = {generate_driver_code() for _ in range(1000)}
    assert len(codes) == 1000
