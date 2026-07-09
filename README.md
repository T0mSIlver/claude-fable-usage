# claude-fable-usage

A Claude Code status line showing your **5-hour**, **7-day**, and **Fable weekly** usage limits.

```
5h 17% · 7d 9% · Fable 15%                        ← on Opus/Sonnet, Fable stays quiet
5h 17% · 7d 9% · FABLE 15% ▰▱▱▱▱▱▱▱ · 6d 9h       ← on Fable, it gets loud
```

Percentages are colour-coded: green under 50%, yellow from 50%, red from 80%.

## Does it remove any default status line items?

One, and it's a hint rather than information: `? for shortcuts`. The custom line is
appended as an extra row *below* the existing footer, not in place of it. Captured frames
with and without it configured, same terminal width:

```
without:   ⏸ manual mode on · ? for shortcuts    ● high · /effort
           /rc connecting…

with:      ⏸ manual mode on                      ● high · /effort
           /rc connecting…
           5h 21% · 7d 10% · Fable 15%
```

That is the whole of it, and it's deliberate rather than incidental — the footer component
takes a `suppressHint` prop which is set when a custom `statusLine` is configured, and that
prop gates only the hint. Permission mode, effort level, the remote-control indicator, and
the `X% context used` / `Context low (…)` line are all rendered independently and survive.

Vim mode survives too: Claude Code keeps drawing its own `-- INSERT --` indicator. If you'd
rather your status line owned that, set `hideVimModeIndicator: true` *inside* the
`statusLine` object.

## Install

Pick whichever of these you like. They all do the same two things: drop `statusline.py`
somewhere, and point `statusLine` in `settings.json` at it. Nothing needs restarting —
Claude Code picks up settings live, so the bar changes within a few seconds.

### The one-liner

```sh
curl -fsSL https://raw.githubusercontent.com/T0mSIlver/claude-fable-usage/main/install.sh | bash
```

Installs `statusline.py` to `~/.claude/`, wires up `settings.json`, and backs the old one
up to `settings.json.bak`. Your other settings are preserved.

If piping a script into `bash` makes you uneasy — it should — read it first:
[`install.sh`](install.sh) is 30 lines.

### Let Claude Code do it

Paste this into any Claude Code session:

```
Install the status line from https://github.com/T0mSIlver/claude-fable-usage
```

There's no special mechanism here; Claude just reads the repo and edits your
`settings.json`. Handy if you want it to explain what it's changing before it does.

### From a clone

```sh
git clone https://github.com/T0mSIlver/claude-fable-usage
cd claude-fable-usage && ./install.sh
```

The `statusLine` then points into the clone, so `git pull` is enough to update. The other
two methods copy the script to `~/.claude/` instead, and you'd rerun the installer.

### By hand

Save `statusline.py` wherever you like, `chmod +x` it, and add this to
`~/.claude/settings.json` (creating the file as `{}` if it doesn't exist):

```json
{
  "statusLine": {
    "type": "command",
    "command": "/absolute/path/to/statusline.py",
    "refreshInterval": 10
  }
}
```

The path must be absolute — `~` is not expanded here.

### Uninstall

Delete the `statusLine` key from `~/.claude/settings.json`, or restore the backup the
installer left at `~/.claude/settings.json.bak`. You can also remove the cache it keeps at
`~/.claude/fable-usage-cache.json`.

## Why the Fable number needs a network call

Claude Code passes the status line a JSON blob on stdin. It contains the 5-hour and
7-day windows, but the model-scoped weekly windows are hard-coded out of it — in
`2.1.205` the payload is built as:

```js
k = { ...x.five_hour && {five_hour:{…}}, ...x.seven_day && {seven_day:{…}} }
```

So the Fable weekly limit is read from `GET /api/oauth/usage`, the same endpoint the
`/usage` dialog uses, where it arrives as a `weekly_scoped` entry:

```json
{ "kind": "weekly_scoped", "percent": 15,
  "scope": { "model": { "display_name": "Fable" } } }
```

That response is cached in `~/.claude/fable-usage-cache.json` and refreshed by a detached
child process, so rendering never blocks on the network (~30ms). Concurrent sessions
coordinate through an `O_EXCL` lock, so eight simultaneous status lines produce exactly
one HTTP request.

The endpoint rate-limits hard, and answers a 429 with `retry-after: 0`, which is no
guidance at all. So: the cache lives for 5 minutes (a weekly window doesn't move faster
than that — at most 12 requests/hour per machine), and a failed fetch keeps the last known
numbers while parking the refresh for 5 minutes, or for a *positive* `Retry-After` clamped
to 1–15 minutes. A failed fetch never blanks the display; the 5h/7d segments don't even
notice, since they come from stdin.

The 5h and 7d numbers still come straight from stdin — no network, always current. The
cache is only a fallback for those, used before the first API response of a session.

## Requirements

- Python 3.9+, standard library only.
- A Claude subscription authenticated via OAuth.

Works on macOS and Linux. Current Claude Code (2.1.x) writes its OAuth token to
`~/.claude/.credentials.json` on every platform — the Keychain backend is dead code, its
write path sits behind `if (false)` and its read path has no callers. Older macOS installs
that still hold the token in the Keychain are handled by a fallback that reads

```sh
security find-generic-password -a "$USER" -s "Claude Code-credentials" -w
```

which is how Claude Code itself used to fetch it. (Under a custom `CLAUDE_CONFIG_DIR` the
service name gains a `-<sha256(dir)[:8]>` suffix; that's reproduced too.) If the Keychain
prompts for access, it does so at most once per backoff window, in the background child.

If neither source yields a token the Fable segment shows `--` and the 5h/7d segments keep
working, since those never need the network. If your plan has no Fable weekly limit, that
segment is omitted entirely.
