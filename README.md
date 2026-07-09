# claude-fable-usage

A Claude Code status line showing your **5-hour**, **7-day**, and **Fable weekly** usage limits.

```
5h 17% · 7d 9% · Fable 15%                        ← on Opus/Sonnet, Fable stays quiet
5h 17% · 7d 9% · FABLE 15% ▰▱▱▱▱▱▱▱ · 6d 9h       ← on Fable, it gets loud
```

Percentages are colour-coded: green under 50%, yellow from 50%, red from 80%.

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

The 5h and 7d numbers still come straight from stdin — no network, always current. The
cache is only a fallback for those, used before the first API response of a session.

## Requirements

- Python 3 (stdlib only)
- A Claude subscription authenticated via OAuth — the token is read from
  `~/.claude/.credentials.json`. On macOS, where Claude Code stores credentials in the
  Keychain instead, the Fable segment will show `--` and the 5h/7d segments still work.

If your plan has no Fable weekly limit, that segment is omitted entirely.
