#!/usr/bin/env python3
"""Claude Code status line: context window, plus 5-hour, 7-day and Fable limits.

Claude Code hands the status line a JSON blob on stdin that carries the context
window and the 5-hour and 7-day windows, but never the model-scoped weekly
windows. Those only exist on GET /api/oauth/usage, so the Fable number is
fetched from there and cached. The fetch happens in a detached child process;
the status line itself never blocks on the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
CREDENTIALS = CLAUDE_DIR / ".credentials.json"
CACHE = CLAUDE_DIR / "fable-usage-cache.json"
LOCK = CLAUDE_DIR / "fable-usage-cache.lock"

# Overridable so the test suite can point this at a local server.
USAGE_URL = os.environ.get(
    "CLAUDE_FABLE_USAGE_URL", "https://api.anthropic.com/api/oauth/usage"
)
# The endpoint rate-limits aggressively, and a weekly window barely moves, so
# there is nothing to gain from refreshing often.
CACHE_TTL = 300  # seconds before the cached usage snapshot is refetched
LOCK_TTL = 30  # a refresh older than this is assumed dead
HTTP_TIMEOUT = 5
BACKOFF = 300  # after a failed fetch, wait this long before trying again
MIN_BACKOFF, MAX_BACKOFF = 60, 900  # bounds on a server-supplied Retry-After

# Fable's *included* access on paid plans ends on July 12, 2026 — extended from
# the original July 7 cutoff after the announcement drew complaints. After it,
# Fable is billed as metered usage credits rather than counting against the
# subscription's weekly limits. Anthropic calls the change temporary and capacity
# driven, so this is a countdown to a billing change, not to the model's
# retirement: `claude-fable-5` is on no published deprecation schedule.
#
# Anthropic never published an hour or a timezone, only the date, so we count
# down to the end of that day in UTC. Override with CLAUDE_FABLE_CUTOFF (an
# ISO-8601 stamp) if a precise time surfaces, or set it empty to drop the
# segment.
FABLE_CUTOFF = os.environ.get("CLAUDE_FABLE_CUTOFF", "2026-07-13T00:00:00Z")
CUTOFF_RED, CUTOFF_YELLOW = 86400, 3 * 86400  # seconds left before it gets loud

RESET, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"
GREEN, YELLOW, RED = "\x1b[32m", "\x1b[33m", "\x1b[31m"
SEP = f"{DIM} · {RESET}"


# --------------------------------------------------------------------------
# usage snapshot
# --------------------------------------------------------------------------


def keychain_service() -> str:
    """Mirrors how Claude Code names its Keychain item.

    'Claude Code-credentials', plus a hash of the config dir when the user has
    pointed CLAUDE_CONFIG_DIR somewhere non-default.
    """
    service = "Claude Code-credentials"
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        normalised = unicodedata.normalize("NFC", config_dir)
        digest = hashlib.sha256(normalised.encode()).hexdigest()[:8]
        service = f"{service}-{digest}"
    return service


def keychain_account() -> str:
    account = os.environ.get("USER") or ""
    if not account:
        try:
            account = os.getlogin()
        except OSError:
            account = ""
    return account if re.fullmatch(r"[a-zA-Z0-9._-]+", account) else "claude-code-user"


def read_credentials() -> dict | None:
    """The plaintext store, which every current Claude Code writes on all platforms."""
    try:
        return json.loads(CREDENTIALS.read_text())
    except (OSError, ValueError):
        return None


def read_keychain() -> dict | None:
    """Fallback for older macOS installs that still keep credentials in the Keychain."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-a", keychain_account(),
                "-s", keychain_service(),
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=HTTP_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip())
    except ValueError:
        return None


def read_token() -> str | None:
    """First source that actually yields a token wins."""
    for source in (read_credentials, read_keychain):
        creds = source() or {}
        token = (creds.get("claudeAiOauth") or {}).get("accessToken")
        if token:
            return token
    return None


def retry_after_from(error) -> float:
    """Honour a Retry-After header when the server sends a usable one.

    /api/oauth/usage answers a 429 with `retry-after: 0`, which is no guidance at
    all — treat anything non-positive as absent rather than as "retry now".
    """
    try:
        seconds = float(error.headers.get("Retry-After", ""))
    except (AttributeError, TypeError, ValueError):
        return BACKOFF
    if seconds <= 0:
        return BACKOFF
    return min(MAX_BACKOFF, max(MIN_BACKOFF, seconds))


def fetch_usage() -> tuple[dict | None, float]:
    """GET /api/oauth/usage, reduced to the windows we render.

    Returns (snapshot, backoff). The endpoint rate-limits, so a failure has to
    park us for a while rather than let every render retry.
    """
    token = read_token()
    if not token:
        return None, BACKOFF

    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "user-agent": "claude-fable-usage-statusline",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        return None, retry_after_from(error)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None, BACKOFF

    snapshot: dict = {"fetched_at": time.time()}
    for key in ("five_hour", "seven_day"):
        window = payload.get(key)
        if isinstance(window, dict):
            snapshot[key] = {
                "percent": window.get("utilization"),
                "resets_at": window.get("resets_at"),
            }

    # Model-scoped weekly windows (Fable, Opus, ...) only appear in limits[].
    scoped = {}
    for limit in payload.get("limits") or []:
        if limit.get("kind") != "weekly_scoped":
            continue
        model = (limit.get("scope") or {}).get("model") or {}
        name = model.get("display_name")
        if name:
            scoped[name] = {
                "percent": limit.get("percent"),
                "resets_at": limit.get("resets_at"),
            }
    snapshot["model_scoped"] = scoped
    return snapshot, 0.0


def write_cache(snapshot: dict) -> None:
    tmp = CACHE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(snapshot))
        tmp.replace(CACHE)
    except OSError:
        pass


def refresh_cache() -> None:
    """Fetch and atomically replace the cache. Runs in the detached child."""
    snapshot, backoff = fetch_usage()
    if snapshot is None:
        # Keep whatever numbers we already had; just stop asking for a while.
        stale = read_cache()
        stale["retry_after"] = time.time() + backoff
        write_cache(stale)
        return
    write_cache(snapshot)


def read_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return {}


def acquire_lock() -> bool:
    """O_EXCL create, so concurrent status lines can't both start a refresh."""
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        pass
    except OSError:
        return False

    # A lock this old belongs to a refresh that died; steal it.
    try:
        if time.time() - LOCK.stat().st_mtime < LOCK_TTL:
            return False
        LOCK.unlink()
        os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except OSError:
        return False


def should_refresh(cache: dict) -> bool:
    if time.time() - cache.get("fetched_at", 0) < CACHE_TTL:
        return False
    return time.time() >= cache.get("retry_after", 0)


def spawn_refresh_if_stale(cache: dict) -> None:
    """Kick off a background refresh, at most one at a time."""
    if not should_refresh(cache):
        return
    if not acquire_lock():
        return

    # Another status line may have refreshed between our read and the lock.
    if not should_refresh(read_cache()):
        LOCK.unlink(missing_ok=True)
        return

    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--refresh"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def colour_for(percent: float) -> str:
    if percent >= 80:
        return RED
    if percent >= 50:
        return YELLOW
    return GREEN


def parse_instant(value):
    """Epoch seconds or an ISO-8601 stamp to an aware datetime, or None."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        # Python 3.9's fromisoformat, which is what stock macOS ships, cannot
        # parse a trailing 'Z'.
        target = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return None
    return target if target.tzinfo else target.replace(tzinfo=timezone.utc)


def humanise_delta(seconds: float) -> str:
    """'6d 9h', '4h 12m', '31m' — the remainder is truncated, not rounded."""
    if seconds <= 0:
        return "now"
    days, seconds = divmod(int(seconds), 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def seconds_until(value):
    """Seconds from now until an instant, or None if it can't be worked out."""
    target = parse_instant(value)
    if target is None:
        return None
    return (target - datetime.now(timezone.utc)).total_seconds()


def humanise_reset(resets_at) -> str:
    """'4h 12m' until the window resets, or '' if it can't be worked out."""
    seconds = seconds_until(resets_at)
    return "" if seconds is None else humanise_delta(seconds)


def number(value):
    """A JSON number, or None for anything else.

    Every number here arrives from JSON — stdin, or a cache file some other
    process wrote — so none of it is really typed. A string where a token count
    belongs used to reach `count < 1_000` and take the whole status line down
    with a TypeError.

    bool is excluded deliberately, despite being an int subclass: `true` where a
    percentage belongs is malformed input, not a confident 1%.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def humanise_tokens(count: float) -> str:
    """'947', '24.5k', '132k', '1M' — narrow enough to sit in a status line."""
    if count < 1_000:
        return str(int(count))
    if count < 1_000_000:
        thousands = count / 1_000
        return f"{thousands:.0f}k" if thousands >= 100 else f"{thousands:.1f}k"
    millions = count / 1_000_000
    return f"{millions:.0f}M" if millions == int(millions) else f"{millions:.1f}M"


def context_used_tokens(window: dict):
    """The input side of the context: prompt plus both cache halves.

    That is exactly the sum Claude Code takes its own percentage against, so the
    tokens we print and the percentage we print always agree. Output tokens are
    deliberately excluded.

    A current_usage carrying no usable number at all — empty, or every counter
    malformed — is unmeasured, not zero, and says so. Summing it to 0 would dress
    an unknown up as a confident 0%, which is what this function's caller is at
    pains to avoid.
    """
    total = number(window.get("total_input_tokens"))
    if total is not None:
        return total
    usage = window.get("current_usage")
    if not isinstance(usage, dict):
        return None
    counters = [
        number(usage.get(key))
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    ]
    if all(count is None for count in counters):
        return None
    return sum(count or 0 for count in counters)


def context_percent(window: dict, used, size):
    """Prefer the percentage Claude Code worked out; recompute only if it's absent.

    A missing percentage alongside a missing current_usage means nothing has been
    measured yet, and total_input_tokens sits at 0 — recomputing from that would
    dress an unknown up as a confident 0%.
    """
    percent = number(window.get("used_percentage"))
    if percent is not None:
        return percent
    if not isinstance(window.get("current_usage"), dict) or used is None or not size:
        return None
    return min(100.0, max(0.0, used / size * 100))


def bar(percent: float, width: int = 8) -> str:
    filled = min(width, max(0, round(percent / 100 * width)))
    return "▰" * filled + "▱" * (width - filled)


def segment(label: str, percent) -> str:
    percent = number(percent)
    if percent is None:
        return f"{DIM}{label} --{RESET}"
    return f"{DIM}{label}{RESET} {colour_for(percent)}{percent:.0f}%{RESET}"


def context_segment(window) -> str:
    """'ctx 24.5k/200k 12%', for whichever model the session is on.

    Claude Code reports no usage before the first reply lands, and none again
    straight after a /compact, so dashes are the honest answer there. Note that
    0% is a real reading and must not be mistaken for a missing one.
    """
    if not isinstance(window, dict):
        return f"{DIM}ctx --{RESET}"

    # A window that is absent, zero, or negative gives nothing to divide by and
    # nothing honest to draw.
    size = number(window.get("context_window_size"))
    if size is not None and size <= 0:
        size = None
    used = context_used_tokens(window)
    percent = context_percent(window, used, size)
    if percent is None or used is None or size is None:
        return f"{DIM}ctx --{RESET}"

    colour = colour_for(percent)
    return (
        f"{DIM}ctx{RESET} {colour}{humanise_tokens(used)}{RESET}"
        f"{DIM}/{humanise_tokens(size)}{RESET} {colour}{percent:.0f}%{RESET}"
    )


def fable_segment(window: dict | None, label: str, active: bool) -> str:
    """The Fable weekly window, loud when the session is actually on Fable."""
    if not isinstance(window, dict):
        return f"{DIM}{label} --{RESET}"

    percent = number(window.get("percent"))
    if percent is None:
        return f"{DIM}{label} --{RESET}"

    if not active:
        return f"{DIM}{label} {percent:.0f}%{RESET}"

    colour = colour_for(percent)
    reset_in = humanise_reset(window.get("resets_at"))
    tail = f" {DIM}·{RESET} {DIM}{reset_in}{RESET}" if reset_in else ""
    return (
        f"{BOLD}{colour}{label.upper()} {percent:.0f}%{RESET} "
        f"{colour}{bar(percent)}{RESET}{tail}"
    )


def cutoff_colour(seconds: float) -> str:
    """Inverted against colour_for: here a *small* number is the alarming one."""
    if seconds < CUTOFF_RED:
        return RED
    if seconds < CUTOFF_YELLOW:
        return YELLOW
    return GREEN


def cutoff_segment(cutoff=None):
    """'sub ends 2d 8h' — what is left of Fable's included subscription access.

    None once the deadline passes, or when it can't be parsed. A countdown frozen
    at zero would be worse than no segment, and what replaces the subscription —
    metered credits — is not a window this status line can measure.
    """
    seconds = seconds_until(FABLE_CUTOFF if cutoff is None else cutoff)
    if seconds is None or seconds <= 0:
        return None
    colour = cutoff_colour(seconds)
    return f"{DIM}sub ends{RESET} {colour}{humanise_delta(seconds)}{RESET}"


def window_percent(stdin_window, cached_window) -> tuple:
    """Prefer the value Claude Code handed us; fall back to the cache.

    A stdin value that isn't a number is treated as absent, so a malformed
    payload falls back to the cache rather than blanking the segment.
    """
    if isinstance(stdin_window, dict) and number(stdin_window.get("used_percentage")) is not None:
        return stdin_window["used_percentage"], stdin_window.get("resets_at")
    if isinstance(cached_window, dict):
        return number(cached_window.get("percent")), cached_window.get("resets_at")
    return None, None


def main() -> None:
    if "--refresh" in sys.argv:
        try:
            refresh_cache()
        finally:
            LOCK.unlink(missing_ok=True)
        return

    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}

    cache = read_cache()
    spawn_refresh_if_stale(cache)

    limits = payload.get("rate_limits") or {}
    five_hour, _ = window_percent(limits.get("five_hour"), cache.get("five_hour"))
    seven_day, _ = window_percent(limits.get("seven_day"), cache.get("seven_day"))

    scoped = cache.get("model_scoped") or {}
    fable_key = next((k for k in scoped if "fable" in k.lower()), None)
    fable_label = fable_key or "fable"
    fable_window = scoped.get(fable_key) if fable_key else None

    model = payload.get("model") or {}
    identity = f"{model.get('id', '')} {model.get('display_name', '')}".lower()
    on_fable = "fable" in identity

    parts = []
    # Claude Code only started sending this window; older ones simply omit it,
    # and a permanent 'ctx --' would be worse than no segment at all.
    if "context_window" in payload:
        parts.append(context_segment(payload["context_window"]))

    parts += [segment("5h", five_hour), segment("7d", seven_day)]
    if fable_window is not None or on_fable:
        parts.append(fable_segment(fable_window, fable_label, on_fable))
        # Gated with the Fable segment: a plan that never had Fable included has
        # nothing to lose on the cutoff date, so the countdown is only noise.
        countdown = cutoff_segment()
        if countdown:
            parts.append(countdown)

    sys.stdout.write(SEP.join(parts))


if __name__ == "__main__":
    main()
