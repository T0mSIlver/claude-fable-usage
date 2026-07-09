"""The pure formatting functions."""

import re
import time


def test_colour_thresholds(sl):
    assert sl.colour_for(0) == sl.GREEN
    assert sl.colour_for(49.9) == sl.GREEN
    assert sl.colour_for(50) == sl.YELLOW
    assert sl.colour_for(79.9) == sl.YELLOW
    assert sl.colour_for(80) == sl.RED
    assert sl.colour_for(100) == sl.RED


def test_bar_is_always_the_requested_width(sl):
    for percent in (0, 1, 33, 50, 99, 100):
        assert len(sl.bar(percent)) == 8
    assert sl.bar(0) == "▱" * 8
    assert sl.bar(100) == "▰" * 8
    assert sl.bar(50) == "▰" * 4 + "▱" * 4


def test_bar_clamps_out_of_range(sl):
    assert sl.bar(-10) == "▱" * 8
    assert sl.bar(500) == "▰" * 8


def test_humanise_reset_accepts_epoch_and_iso(sl):
    # A few seconds of slack: the remainder is truncated, not rounded.
    soon = time.time() + 3600 * 4 + 60 * 12 + 5
    assert sl.humanise_reset(soon) == "4h 12m"


def test_humanise_reset_parses_trailing_z(sl):
    """Python 3.9's fromisoformat cannot parse a bare 'Z'."""
    future = time.gmtime(time.time() + 86400 * 2)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", future)
    assert re.fullmatch(r"\d+d \d+h", sl.humanise_reset(stamp))


def test_humanise_reset_parses_the_real_payload_shape(sl):
    """Six-digit fractional seconds plus an explicit offset, as the API sends."""
    future = time.gmtime(time.time() + 86400 * 3)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", future) + ".259946+00:00"
    assert re.fullmatch(r"\d+d \d+h", sl.humanise_reset(stamp))


def test_humanise_reset_edge_cases(sl):
    assert sl.humanise_reset(None) == ""
    assert sl.humanise_reset("not a date") == ""
    assert sl.humanise_reset(time.time() - 3600) == "now"


def test_segment_renders_missing_as_dashes(sl):
    assert "--" in sl.segment("5h", None)
    assert "17%" in sl.segment("5h", 17.4)


def test_fable_segment_is_quiet_when_inactive(sl):
    out = sl.fable_segment({"percent": 15, "resets_at": None}, "Fable", active=False)
    assert "15%" in out
    assert sl.BOLD not in out
    assert "▰" not in out


def test_fable_segment_is_loud_when_active(sl):
    out = sl.fable_segment({"percent": 15, "resets_at": None}, "Fable", active=True)
    assert "FABLE 15%" in out
    assert sl.BOLD in out
    assert "▰" in out


def test_fable_segment_handles_absent_window(sl):
    assert "--" in sl.fable_segment(None, "Fable", active=True)
    assert "--" in sl.fable_segment({"percent": None}, "Fable", active=False)


def test_window_percent_prefers_stdin_over_cache(sl):
    stdin = {"used_percentage": 22, "resets_at": "a"}
    cache = {"percent": 99, "resets_at": "b"}
    assert sl.window_percent(stdin, cache) == (22, "a")


def test_window_percent_falls_back_to_cache(sl):
    cache = {"percent": 99, "resets_at": "b"}
    assert sl.window_percent(None, cache) == (99, "b")
    assert sl.window_percent({"used_percentage": None}, cache) == (99, "b")


def test_window_percent_with_nothing(sl):
    assert sl.window_percent(None, None) == (None, None)
