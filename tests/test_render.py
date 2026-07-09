"""The pure formatting functions."""

import re
import time

import pytest


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


def test_humanise_tokens(sl):
    assert sl.humanise_tokens(0) == "0"
    assert sl.humanise_tokens(947) == "947"
    assert sl.humanise_tokens(24_500) == "24.5k"
    assert sl.humanise_tokens(132_000) == "132k"
    assert sl.humanise_tokens(200_000) == "200k"
    assert sl.humanise_tokens(1_000_000) == "1M"
    assert sl.humanise_tokens(1_200_000) == "1.2M"


def test_context_segment_renders_tokens_and_percent(sl):
    out = sl.context_segment({
        "total_input_tokens": 24_500,
        "context_window_size": 200_000,
        "used_percentage": 12,
        "current_usage": {"input_tokens": 24_500},
    })
    assert "ctx" in out
    assert "24.5k" in out
    assert "200k" in out
    assert "12%" in out


def test_context_segment_handles_a_1m_window(sl):
    out = sl.context_segment({
        "total_input_tokens": 250_000,
        "context_window_size": 1_000_000,
        "used_percentage": 25,
        "current_usage": {"input_tokens": 250_000},
    })
    assert "250k" in out and "1M" in out and "25%" in out


def test_context_segment_dashes_before_the_first_reply(sl):
    """current_usage is null until a response lands, and again after /compact."""
    assert "--" in sl.context_segment({
        "total_input_tokens": 0,
        "context_window_size": 200_000,
        "used_percentage": None,
        "current_usage": None,
    })
    assert "--" in sl.context_segment(None)
    assert "--" in sl.context_segment("unexpectedly a string")
    assert "--" in sl.context_segment({})


def test_context_segment_treats_zero_percent_as_a_real_reading(sl):
    """0% is a value, not a missing number."""
    out = sl.context_segment({
        "total_input_tokens": 300,
        "context_window_size": 200_000,
        "used_percentage": 0,
        "current_usage": {"input_tokens": 300},
    })
    assert "--" not in out
    assert "0%" in out and "300" in out


def test_context_segment_computes_percent_when_absent(sl):
    out = sl.context_segment({
        "context_window_size": 200_000,
        "current_usage": {
            "input_tokens": 10_000,
            "cache_creation_input_tokens": 5_000,
            "cache_read_input_tokens": 35_000,
        },
    })
    # 50k of 200k, summed from the three input-side counters.
    assert "50.0k" in out and "25%" in out


def test_context_segment_colours_by_fill(sl):
    def render(percent):
        return sl.context_segment({
            "total_input_tokens": 2_000 * percent,
            "context_window_size": 200_000,
            "used_percentage": percent,
            "current_usage": {"input_tokens": 1},
        })

    assert sl.GREEN in render(10)
    assert sl.YELLOW in render(60)
    assert sl.RED in render(90)


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


def test_number_admits_only_json_numbers(sl):
    assert sl.number(0) == 0
    assert sl.number(12.5) == 12.5
    assert sl.number(-3) == -3
    assert sl.number(None) is None
    assert sl.number("half") is None
    assert sl.number([1]) is None
    assert sl.number({}) is None


def test_number_rejects_bools(sl):
    """bool subclasses int, and `true` where a percentage belongs is not 1%."""
    assert sl.number(True) is None
    assert sl.number(False) is None


@pytest.mark.parametrize("window", [
    {"total_input_tokens": "lots", "context_window_size": 200_000, "used_percentage": 12},
    {"total_input_tokens": 1_000, "context_window_size": "big", "used_percentage": 12},
    {"total_input_tokens": 1_000, "context_window_size": 200_000, "used_percentage": "half"},
    {"context_window_size": 200_000, "current_usage": {"input_tokens": "x"}},
    {"total_input_tokens": 1_000, "context_window_size": -200_000, "used_percentage": 12},
    {"total_input_tokens": True, "context_window_size": 200_000, "used_percentage": True},
])
def test_context_segment_dashes_on_wrongly_typed_numbers(sl, window):
    """A string where a number belongs used to crash the whole status line."""
    assert "--" in sl.context_segment(window)


def test_segment_dashes_on_a_wrongly_typed_percent(sl):
    assert "--" in sl.segment("5h", "half")
    assert "--" in sl.segment("5h", True)
    assert "--" in sl.segment("5h", [1])


def test_fable_segment_dashes_on_a_wrongly_typed_percent(sl):
    assert "--" in sl.fable_segment({"percent": "lots"}, "Fable", active=True)
    assert "--" in sl.fable_segment({"percent": True}, "Fable", active=False)
    assert "--" in sl.fable_segment("a corrupt cache entry", "Fable", active=True)


def test_window_percent_ignores_a_wrongly_typed_stdin_value(sl):
    """Malformed stdin falls back to the cache rather than blanking the segment."""
    cache = {"percent": 99, "resets_at": "b"}
    assert sl.window_percent({"used_percentage": "half"}, cache) == (99, "b")
    assert sl.window_percent({"used_percentage": True}, cache) == (99, "b")
    assert sl.window_percent({"used_percentage": 22}, cache) == (22, None)


def test_window_percent_ignores_a_wrongly_typed_cache_value(sl):
    assert sl.window_percent(None, {"percent": "lots"}) == (None, None)


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
