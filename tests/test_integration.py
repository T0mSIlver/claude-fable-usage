"""End-to-end behaviour of the script as Claude Code actually runs it."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import SCRIPT

PAYLOAD = {
    "five_hour": {"utilization": 22.0, "resets_at": "2026-07-09T12:20:00+00:00"},
    "seven_day": {"utilization": 10.0, "resets_at": "2026-07-15T20:00:00+00:00"},
    "limits": [{
        "kind": "weekly_scoped",
        "percent": 15,
        "resets_at": "2026-07-15T20:00:00+00:00",
        "scope": {"model": {"display_name": "Fable"}},
    }],
}

FABLE_STDIN = json.dumps({"model": {"id": "claude-fable-5", "display_name": "Fable"}})


def render(stdin, config_dir, url=None, timeout=30, cutoff=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(config_dir), "CLAUDE_CONFIG_DIR": str(config_dir)}
    if url:
        env["CLAUDE_FABLE_USAGE_URL"] = url
    if cutoff is not None:
        env["CLAUDE_FABLE_CUTOFF"] = cutoff
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=stdin, capture_output=True, text=True, env=env, timeout=timeout,
    )


def iso_in(seconds):
    """An ISO stamp `seconds` from now, so a test never depends on today's date."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


# --- never crash -----------------------------------------------------------
#
# A status line that exits non-zero, or prints nothing, leaves a broken footer.


@pytest.mark.parametrize("stdin", [
    "",
    "not json at all",
    "{}",
    '{"model": null, "rate_limits": null}',
    '{"rate_limits": {"five_hour": "unexpectedly a string"}}',
    FABLE_STDIN,
])
def test_render_survives_any_stdin(tmp_path, stdin):
    result = render(stdin, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "5h" in result.stdout


def test_context_window_is_rendered(tmp_path):
    result = render(json.dumps({
        "context_window": {
            "total_input_tokens": 24_500,
            "total_output_tokens": 120,
            "context_window_size": 200_000,
            "used_percentage": 12,
            "remaining_percentage": 88,
            "current_usage": {
                "input_tokens": 5_047,
                "output_tokens": 178,
                "cache_creation_input_tokens": 3_227,
                "cache_read_input_tokens": 16_226,
            },
        },
    }), tmp_path)
    assert result.returncode == 0, result.stderr
    assert "24.5k" in result.stdout
    assert "200k" in result.stdout
    assert "12%" in result.stdout


def test_context_segment_is_absent_on_older_claude_code(tmp_path):
    """No context_window key at all: the line looks exactly as it used to."""
    result = render('{"rate_limits": {"five_hour": {"used_percentage": 12}}}', tmp_path)
    assert result.returncode == 0, result.stderr
    assert "ctx" not in result.stdout


@pytest.mark.parametrize("context_window", [
    None,
    "unexpectedly a string",
    {},
    {"context_window_size": 0, "used_percentage": 5},
    {"context_window_size": 200_000, "used_percentage": None, "current_usage": None},
    # Wrongly typed numbers. Each of these used to raise a TypeError and take the
    # status line down with it, leaving a broken footer.
    {"total_input_tokens": "lots", "context_window_size": 200_000, "used_percentage": 12},
    {"total_input_tokens": 1_000, "context_window_size": "big", "used_percentage": 12},
    {"total_input_tokens": 1_000, "context_window_size": 200_000, "used_percentage": "half"},
    {"total_input_tokens": 1_000, "context_window_size": -200_000, "used_percentage": 12},
    {"context_window_size": 200_000, "current_usage": {"input_tokens": "x"}},
    {"context_window_size": [], "used_percentage": {}},
])
def test_render_survives_any_context_window(tmp_path, context_window):
    result = render(json.dumps({"context_window": context_window}), tmp_path)
    assert result.returncode == 0, result.stderr
    assert "ctx" in result.stdout
    assert "5h" in result.stdout


@pytest.mark.parametrize("rate_limits", [
    {"five_hour": "unexpectedly a string"},
    {"five_hour": {"used_percentage": "half"}},
    {"five_hour": {"used_percentage": [1]}},
    {"seven_day": {"used_percentage": True}},
])
def test_render_survives_wrongly_typed_rate_limits(tmp_path, rate_limits):
    """The same defect lived one level deeper in the 5h/7d path."""
    result = render(json.dumps({"rate_limits": rate_limits}), tmp_path)
    assert result.returncode == 0, result.stderr
    assert "5h" in result.stdout


def test_render_survives_a_corrupt_cached_fable_percent(tmp_path):
    (tmp_path / "fable-usage-cache.json").write_text(json.dumps({
        "fetched_at": time.time(),
        "model_scoped": {"Fable": {"percent": "lots"}},
    }))
    result = render(FABLE_STDIN, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "--" in result.stdout


# --- the subscription countdown --------------------------------------------
#
# Every case pins the cutoff explicitly: a test that leaned on the built-in date
# would start failing of its own accord once that date passes.


def cached_fable(tmp_path):
    (tmp_path / "fable-usage-cache.json").write_text(json.dumps({
        "fetched_at": time.time(),
        "model_scoped": {"Fable": {"percent": 15}},
    }))


def test_countdown_renders_on_a_fable_session(tmp_path):
    result = render(FABLE_STDIN, tmp_path, cutoff=iso_in(86400 * 2 + 60))
    assert result.returncode == 0, result.stderr
    assert "sub ends" in result.stdout
    assert "2d 0h" in result.stdout


def test_countdown_accompanies_a_quiet_fable_segment(tmp_path):
    """Not on Fable, but the plan has the window: the deadline still applies."""
    cached_fable(tmp_path)
    result = render(
        json.dumps({"model": {"id": "claude-opus-4-8", "display_name": "Opus 4.8"}}),
        tmp_path,
        cutoff=iso_in(86400 * 2),
    )
    assert result.returncode == 0, result.stderr
    assert "Fable 15%" in result.stdout
    assert "sub ends" in result.stdout


def test_countdown_is_absent_without_a_fable_segment(tmp_path):
    """A plan that never had Fable included loses nothing on the date."""
    result = render(
        json.dumps({"model": {"id": "claude-opus-4-8", "display_name": "Opus 4.8"}}),
        tmp_path,
        cutoff=iso_in(86400 * 2),
    )
    assert result.returncode == 0, result.stderr
    assert "fable" not in result.stdout.lower()
    assert "sub ends" not in result.stdout


def test_countdown_disappears_once_the_deadline_passes(tmp_path):
    """The Fable segment survives it; only the countdown goes."""
    cached_fable(tmp_path)
    result = render(FABLE_STDIN, tmp_path, cutoff="2020-01-01T00:00:00Z")
    assert result.returncode == 0, result.stderr
    assert "sub ends" not in result.stdout
    assert "FABLE 15%" in result.stdout


@pytest.mark.parametrize("cutoff", ["", "garbage", "2026-13-45T99:99:99Z", "1800000000"])
def test_render_survives_any_cutoff(tmp_path, cutoff):
    result = render(FABLE_STDIN, tmp_path, cutoff=cutoff)
    assert result.returncode == 0, result.stderr
    assert "sub ends" not in result.stdout
    assert "5h" in result.stdout


def test_render_works_with_no_token_and_no_cache(tmp_path):
    """No credentials anywhere: 5h/7d still render, Fable is simply absent."""
    result = render('{"rate_limits": {"five_hour": {"used_percentage": 12}}}', tmp_path)
    assert result.returncode == 0, result.stderr
    assert "12%" in result.stdout


def test_stdin_values_win_over_a_stale_cache(tmp_path):
    (tmp_path / "fable-usage-cache.json").write_text(json.dumps({
        "fetched_at": time.time(),
        "five_hour": {"percent": 99.0},
        "model_scoped": {"Fable": {"percent": 15}},
    }))
    result = render('{"rate_limits": {"five_hour": {"used_percentage": 3}}}', tmp_path)
    assert "3%" in result.stdout
    assert "99%" not in result.stdout


def test_weekly_reset_rides_along_with_the_fable_percent(tmp_path):
    """On Fable or not, the percent is a share of a week — show what's left of it."""
    (tmp_path / "fable-usage-cache.json").write_text(json.dumps({
        "fetched_at": time.time(),
        "model_scoped": {
            "Fable": {"percent": 15, "resets_at": iso_in(86400 * 3 + 7200 + 60)},
        },
    }))
    cutoff = iso_in(86400 * 5 + 60)
    quiet = render('{"model": {"id": "claude-opus-4-8"}}', tmp_path, cutoff=cutoff)
    loud = render(FABLE_STDIN, tmp_path, cutoff=cutoff)

    for result in (quiet, loud):
        assert result.returncode == 0, result.stderr
        assert "resets 3d 2h" in result.stdout
        assert "sub ends" in result.stdout and "5d 0h" in result.stdout


def test_fable_segment_is_bold_only_on_a_fable_session(tmp_path):
    (tmp_path / "fable-usage-cache.json").write_text(json.dumps({
        "fetched_at": time.time(),
        "model_scoped": {"Fable": {"percent": 15}},
    }))
    quiet = render('{"model": {"id": "claude-opus-4-8"}}', tmp_path).stdout
    loud = render(FABLE_STDIN, tmp_path).stdout

    assert "Fable 15%" in quiet and "\x1b[1m" not in quiet
    assert "FABLE 15%" in loud and "\x1b[1m" in loud


# --- one request per stampede ----------------------------------------------


class CountingHandler(BaseHTTPRequestHandler):
    requests = 0
    lock = threading.Lock()

    def do_GET(self):
        with CountingHandler.lock:
            CountingHandler.requests += 1
        body = json.dumps(PAYLOAD).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def counting_server():
    CountingHandler.requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/usage", CountingHandler
    server.shutdown()
    server.server_close()


def test_concurrent_renders_make_exactly_one_request(tmp_path, counting_server):
    """Eight status lines starting at once must not stampede the endpoint."""
    url, handler = counting_server
    (tmp_path / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "test-token"}})
    )

    procs = [
        subprocess.Popen(
            [sys.executable, str(SCRIPT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                 "CLAUDE_CONFIG_DIR": str(tmp_path), "CLAUDE_FABLE_USAGE_URL": url},
        )
        for _ in range(8)
    ]
    for proc in procs:
        out, err = proc.communicate(FABLE_STDIN, timeout=30)
        assert proc.returncode == 0, err
        assert "5h" in out

    # The refresh is a detached child, so wait for it to land rather than reap it.
    cache = tmp_path / "fable-usage-cache.json"
    deadline = time.time() + 20
    while time.time() < deadline:
        if cache.exists() and "model_scoped" in json.loads(cache.read_text() or "{}"):
            break
        time.sleep(0.1)

    snapshot = json.loads(cache.read_text())
    assert snapshot["model_scoped"]["Fable"]["percent"] == 15
    assert handler.requests == 1, f"stampede: {handler.requests} requests"
    assert not (tmp_path / "fable-usage-cache.lock").exists(), "lock was not released"


def test_a_stale_lock_is_stolen(sl):
    """A refresh that died must not wedge every later render."""
    sl.LOCK.write_text("")
    assert sl.acquire_lock() is False

    old = time.time() - sl.LOCK_TTL - 1
    os.utime(sl.LOCK, (old, old))
    assert sl.acquire_lock() is True
