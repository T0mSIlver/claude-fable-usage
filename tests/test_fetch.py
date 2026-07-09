"""Credential lookup, backoff arithmetic, and parsing the usage payload."""

import email.message
import hashlib
import io
import json
import unicodedata
import urllib.error

import pytest

PAYLOAD = {
    "five_hour": {"utilization": 22.0, "resets_at": "2026-07-09T12:20:00.259922+00:00"},
    "seven_day": {"utilization": 10.0, "resets_at": "2026-07-15T20:00:00.259946+00:00"},
    "limits": [
        {"kind": "weekly", "percent": 10},
        {
            "kind": "weekly_scoped",
            "percent": 15,
            "resets_at": "2026-07-15T20:00:00.260189+00:00",
            "scope": {"model": {"display_name": "Fable"}},
        },
        {
            "kind": "weekly_scoped",
            "percent": 3,
            "resets_at": "2026-07-15T20:00:00+00:00",
            "scope": {"model": {"display_name": "Opus"}},
        },
    ],
}


def http_error(code, headers=None):
    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("http://x", code, "boom", message, None)


# --- backoff ---------------------------------------------------------------


def test_retry_after_zero_is_not_taken_literally(sl):
    """The real endpoint answers 429 with `retry-after: 0`."""
    assert sl.retry_after_from(http_error(429, {"Retry-After": "0"})) == sl.BACKOFF


def test_retry_after_negative_is_ignored(sl):
    assert sl.retry_after_from(http_error(429, {"Retry-After": "-5"})) == sl.BACKOFF


def test_retry_after_absent_or_garbage(sl):
    assert sl.retry_after_from(http_error(500)) == sl.BACKOFF
    assert sl.retry_after_from(http_error(429, {"Retry-After": "soon"})) == sl.BACKOFF
    assert sl.retry_after_from(object()) == sl.BACKOFF


def test_retry_after_is_clamped(sl):
    assert sl.retry_after_from(http_error(429, {"Retry-After": "5"})) == sl.MIN_BACKOFF
    assert sl.retry_after_from(http_error(429, {"Retry-After": "99999"})) == sl.MAX_BACKOFF
    assert sl.retry_after_from(http_error(429, {"Retry-After": "120"})) == 120


# --- credentials -----------------------------------------------------------


def test_token_read_from_plaintext_credentials(sl):
    sl.CREDENTIALS.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))
    assert sl.read_token() == "tok"


def test_credentials_without_a_token_do_not_shadow_the_keychain(sl, monkeypatch):
    """A file holding only mcpOAuth is truthy but tokenless."""
    sl.CREDENTIALS.write_text(json.dumps({"mcpOAuth": {"whatever": 1}}))
    monkeypatch.setattr(sl, "read_keychain", lambda: {"claudeAiOauth": {"accessToken": "kc"}})
    assert sl.read_token() == "kc"


def test_no_token_anywhere(sl, monkeypatch):
    monkeypatch.setattr(sl, "read_keychain", lambda: None)
    assert sl.read_token() is None


def test_keychain_is_skipped_off_darwin(sl, monkeypatch):
    monkeypatch.setattr(sl.sys, "platform", "linux")
    assert sl.read_keychain() is None


def test_keychain_service_name(sl, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert sl.keychain_service() == "Claude Code-credentials"


def test_keychain_service_name_hashes_a_custom_config_dir(sl, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/cfg")
    digest = hashlib.sha256(unicodedata.normalize("NFC", "/tmp/cfg").encode()).hexdigest()[:8]
    assert sl.keychain_service() == f"Claude Code-credentials-{digest}"


@pytest.mark.parametrize("user,expected", [
    ("alice", "alice"),
    ("a.b_c-1", "a.b_c-1"),
    ("has space", "claude-code-user"),
    ("", "claude-code-user"),
])
def test_keychain_account_falls_back_on_odd_usernames(sl, monkeypatch, user, expected):
    monkeypatch.setenv("USER", user)
    monkeypatch.setattr(sl.os, "getlogin", lambda: (_ for _ in ()).throw(OSError))
    assert sl.keychain_account() == expected


# --- fetch -----------------------------------------------------------------


def test_fetch_usage_extracts_the_scoped_weekly_windows(sl, monkeypatch):
    sl.CREDENTIALS.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))
    monkeypatch.setattr(
        sl.urllib.request, "urlopen",
        lambda *a, **k: io.BytesIO(json.dumps(PAYLOAD).encode()),
    )
    snapshot, backoff = sl.fetch_usage()

    assert backoff == 0.0
    assert snapshot["five_hour"]["percent"] == 22.0
    assert snapshot["seven_day"]["percent"] == 10.0
    assert snapshot["model_scoped"]["Fable"]["percent"] == 15
    assert snapshot["model_scoped"]["Opus"]["percent"] == 3
    # kind:"weekly" has no model scope and must not appear
    assert set(snapshot["model_scoped"]) == {"Fable", "Opus"}


def test_fetch_usage_without_a_token_does_not_call_out(sl, monkeypatch):
    monkeypatch.setattr(sl, "read_keychain", lambda: None)

    def explode(*_a, **_k):
        raise AssertionError("should not have made a request")

    monkeypatch.setattr(sl.urllib.request, "urlopen", explode)
    assert sl.fetch_usage() == (None, sl.BACKOFF)


def test_failed_fetch_preserves_the_last_known_numbers(sl, monkeypatch):
    sl.write_cache({"fetched_at": 1, "five_hour": {"percent": 42.0}})
    monkeypatch.setattr(sl, "fetch_usage", lambda: (None, 300))

    sl.refresh_cache()
    cache = sl.read_cache()

    assert cache["five_hour"]["percent"] == 42.0, "a failed fetch must not blank the display"
    assert cache["retry_after"] > 0, "a failed fetch must park the next attempt"


def test_should_refresh_respects_backoff_and_ttl(sl):
    now = sl.time.time()
    assert sl.should_refresh({}) is True
    assert sl.should_refresh({"fetched_at": now}) is False
    assert sl.should_refresh({"fetched_at": 0, "retry_after": now + 100}) is False
    assert sl.should_refresh({"fetched_at": 0, "retry_after": now - 1}) is True
