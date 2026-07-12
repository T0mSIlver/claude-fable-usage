# claude-fable-usage

[![CI](https://github.com/T0mSIlver/claude-fable-usage/actions/workflows/ci.yml/badge.svg)](https://github.com/T0mSIlver/claude-fable-usage/actions/workflows/ci.yml)

A Claude Code status line for your **context window** and your **5-hour**, **7-day**, and
**Fable weekly** usage limits. The Fable segment stays quiet until the session is actually
on Fable.

```
ctx 24.5k/200k 12% · 5h 17% · 7d 9% · Fable 15% · resets 6d 9h · sub ends 3d 7h        ← on Opus/Sonnet
ctx 132k/200k 66% · 5h 17% · 7d 9% · FABLE 15% ▰▱▱▱▱▱▱▱ · resets 6d 9h · sub ends 3d 7h ← on Fable
```

Green under 50%, yellow from 50%, red from 80%.

`resets` is what remains of the current Fable week. It rides along with the percentage in
both forms, because the percentage alone doesn't tell you much: 60% spent is comfortable
with a day to go and alarming with six. It is dropped only when the API sends no reset
timestamp to count to.

The `ctx` segment covers every model, and sizes itself to whatever window the session has —
`200k` normally, `1M` on a `[1m]` model. The token count is the input side of the context
(your prompt plus both cache halves), which is the same figure Claude Code takes its own
percentage against. It reads `ctx --` until the first reply lands, and again right after a
`/compact`, because until then there is genuinely nothing to report. On a Claude Code too
old to send the numbers, the segment is left out rather than showing a permanent `--`.

Claude Code has a context readout of its own, but you will rarely have seen it: the footer
renders nothing until you are within 20k tokens of auto-compact, and only then warns. This
segment is there for the whole session, and counts tokens rather than only percent.

## The `sub ends` countdown

Fable's *included* access on Pro, Max, Team and select Enterprise plans ends on **July 19,
2026** — extended twice now, from July 7 to July 12 and then to the 19th. After it, Fable is
billed as metered usage credits at API rates rather than counting against the subscription's
weekly limits.

So `sub ends` counts down to a **billing change, not a retirement**. `claude-fable-5` sits on
no published [deprecation schedule](https://platform.claude.com/docs/en/about-claude/model-deprecations),
and Anthropic calls the change temporary and capacity-driven, saying it aims to restore Fable
to subscriptions once it has the servers. Read the countdown as *"days until the `Fable`
segment above stops meaning anything"*, because a credit balance is not a weekly window and
this status line cannot show one.

Anthropic published a date but never an hour or a timezone, so the countdown runs to the end
of July 19 in UTC. Point it somewhere else — the next extension, if there is one — or drop
the segment, with an ISO-8601 stamp:

```sh
export CLAUDE_FABLE_CUTOFF=2026-07-19T17:00:00-07:00   # a precise time, if one surfaces
export CLAUDE_FABLE_CUTOFF=                            # empty: no countdown
```

Green until three days out, yellow inside three days, red inside the last twenty-four hours —
inverted against the usage segments, where it is the *large* number that alarms. The segment
removes itself once the deadline passes, rather than sitting at a frozen zero, and it is
gated behind the Fable segment: a plan that never had Fable included has nothing to lose on
the date, and sees no countdown.

## Install

Any of these works. They all do the same two things: drop `statusline.py` somewhere, and
point `statusLine` at it in `settings.json`. Nothing to restart — settings are picked up live.

**One-liner.** Installs to `~/.claude/`, backs the old settings up to `settings.json.bak`,
leaves your other keys alone.

```sh
curl -fsSL https://raw.githubusercontent.com/T0mSIlver/claude-fable-usage/main/install.sh | bash
```

Piping a script into `bash` deserves a look first: [`install.sh`](install.sh).

**Ask Claude Code.** Paste this into any session:

```
Install the status line from https://github.com/T0mSIlver/claude-fable-usage
```

No special mechanism — Claude reads the repo and edits your `settings.json`. Good if you
want it explained before it changes anything.

**From a clone.** `statusLine` points into the clone, so `git pull` is enough to update.

```sh
git clone https://github.com/T0mSIlver/claude-fable-usage
cd claude-fable-usage && ./install.sh
```

**By hand.** `chmod +x statusline.py`, then add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/absolute/path/to/statusline.py",
    "refreshInterval": 10
  }
}
```

The path must be absolute; `~` is not expanded.

**Uninstall.** Delete the `statusLine` key, or restore `settings.json.bak`. The cache sits
at `~/.claude/fable-usage-cache.json`.

## What it displaces

One thing, and it's a hint rather than information: `? for shortcuts`. The line is appended
*below* the footer, not in place of it.

```
without:   ⏸ manual mode on · ? for shortcuts    ● high · /effort
with:      ⏸ manual mode on                      ● high · /effort
           5h 21% · 7d 10% · Fable 15%
```

That's deliberate: the footer takes a `suppressHint` prop, set when a custom `statusLine`
exists, and it gates only the hint. Permission mode, effort level, the `/rc` indicator, the
`X% context used` line, and vim's `-- INSERT --` all survive. To own the vim indicator
yourself, set `hideVimModeIndicator: true` *inside* the `statusLine` object.

## Why Fable needs a network call

Claude Code hands the status line a JSON blob on stdin carrying the 5-hour and 7-day
windows — but the model-scoped weekly windows are hard-coded out of it. In `2.1.205` the
payload is built as:

```js
k = { ...x.five_hour && {five_hour:{…}}, ...x.seven_day && {seven_day:{…}} }
```

So the Fable number comes from `GET /api/oauth/usage`, the endpoint `/usage` itself uses,
where it arrives as a `weekly_scoped` entry:

```json
{ "kind": "weekly_scoped", "percent": 15, "scope": { "model": { "display_name": "Fable" } } }
```

5h and 7d still come straight from stdin: no network, always current. The cache only backs
them up before the session's first API response.

That response is cached and refreshed by a detached child, so rendering never blocks
(~30ms). Concurrent sessions coordinate through an `O_EXCL` lock — eight simultaneous
status lines make exactly one request. The endpoint rate-limits hard and answers a 429 with
`retry-after: 0`, which is no guidance at all, so a non-positive `Retry-After` is ignored.
The cache lives five minutes (at most 12 requests/hour/machine), and a failed fetch keeps
the last known numbers rather than blanking the display.

## Requirements

Python 3.9+, standard library only. A Claude subscription authenticated via OAuth. macOS
and Linux.

Current Claude Code writes its OAuth token to `~/.claude/.credentials.json` on every
platform — the Keychain backend is dead code, its write path behind `if (false)` and its
read path uncalled. Older macOS installs that still hold the token in the Keychain fall
back to `security find-generic-password`, reproducing Claude Code's own service name
(including the `-<sha256(dir)[:8]>` suffix under a custom `CLAUDE_CONFIG_DIR`).

With no token the Fable segment shows `--` and 5h/7d keep working, since those never need
the network. If your plan has no Fable weekly limit, the segment is omitted entirely — and
with it the `sub ends` countdown.

## Development

```sh
python -m pytest -q   # pytest is the only dependency, and only for the tests
ruff check .
```

Beyond the formatting and backoff arithmetic, the suite pins the two invariants most likely
to break silently: eight concurrent renders make exactly one HTTP request, and the script
never exits non-zero no matter what arrives on stdin — a crashing status line leaves a
broken footer. CI runs it on Linux and macOS across Python 3.9–3.13, and re-runs the
published one-liner weekly, so the install path can't rot unnoticed.

## License

MIT — see [LICENSE](LICENSE).
