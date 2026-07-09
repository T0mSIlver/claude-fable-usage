# claude-fable-usage

A Claude Code status line showing your **5-hour**, **7-day**, and **Fable weekly** usage limits.

```
5h 17% · 7d 9% · Fable 15%                        ← on Opus/Sonnet, Fable stays quiet
5h 17% · 7d 9% · FABLE 15% ▰▱▱▱▱▱▱▱ · 6d 9h       ← on Fable, it gets loud
```

Percentages are colour-coded: green under 50%, yellow from 50%, red from 80%.

## Does it replace the default status line?

Almost nothing. The custom line is appended as an extra row *below* the existing footer,
not in place of it. Comparing captured frames with and without it configured, at the same
terminal width, the only casualty is the `? for shortcuts` hint:

```
without:   ⏸ manual mode on · ? for shortcuts    ● high · /effort
           /rc connecting…

with:      ⏸ manual mode on                      ● high · /effort
           /rc connecting…
           5h 21% · 7d 10% · Fable 15%
```

The permission mode, effort level, and remote-control indicator all survive. Model name,
context window, and cost were never in the footer to begin with — they live in the welcome
box and `/status`.

Vim mode is unaffected too: Claude Code keeps drawing its own `-- INSERT --` indicator. If
you'd rather your status line owned that, set `hideVimModeIndicator: true` *inside* the
`statusLine` object.

## Install

```sh
./install.sh
```

That points `statusLine` in `~/.claude/settings.json` at `statusline.py`. Restart Claude Code, or just wait — settings are picked up live.

To do it by hand:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/absolute/path/to/statusline.py",
    "refreshInterval": 10
  }
}
```

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

That response is cached in `~/.claude/fable-usage-cache.json` for 60s and refreshed by a
detached child process, so rendering never blocks on the network (~30ms). Concurrent
sessions coordinate through an `O_EXCL` lock, so eight simultaneous status lines
produce exactly one HTTP request.

The endpoint rate-limits. When a fetch fails, the last known numbers are kept and the
refresh parks for 5 minutes (or whatever `Retry-After` asks for, clamped to 1–15 min)
rather than letting every render retry. A failed fetch never blanks the display.

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
