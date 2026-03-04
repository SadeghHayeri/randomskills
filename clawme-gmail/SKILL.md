---
name: clawme-gmail
description: Google integrations via OAuth — Gmail, Calendar, Drive, Docs, Sheets
metadata:
  openclaw:
    emoji: link
    requires:
      bins: ["clawme"]
      env: ["OPENCLAW_PLATFORM_API_KEY"]
    primaryEnv: OPENCLAW_PLATFORM_API_KEY
---

# clawme — Integration Gateway CLI

Use this skill whenever you need to call an external service API (Google Calendar, Gmail,
Drive, Docs, Sheets, or future providers) on behalf of the user.

`clawme` checks the connection, handles token refresh automatically, and tells you exactly
what to relay to the user if they need to authorize or reconnect.

## When to use

- User asks you to read/write their Google Calendar, Gmail, Drive, Docs, or Sheets
- You need to run any `gog` command against a Google API
- You want to check which external services are currently connected
- User wants the agent to react to incoming emails in real time (use `gmail-watch`)

## Provider aliases

| Alias | Provider |
|-------|----------|
| `gog` | Google   |

## Commands

### Run any `gog` command (Google API access)

`clawme gog` stores Google OAuth credentials and hands off directly to the `gog` binary.
**All `gog` subcommands work transparently.**

```bash
clawme gog <any gog subcommand and args>
```

Examples:

```bash
clawme gog users messages list --user-id=me --max-results=10
clawme gog users calendars list
clawme gog users messages get --user-id=me --id=MESSAGE_ID --format=full
```

### Validate scopes before running gog (recommended)

Pass `--scopes` before `gog` to verify the required scopes are granted.
If they are missing, `clawme` exits with code 3 and prints an actionable setup URL.

```bash
clawme --scopes gmail:readonly gog users messages list --user-id=me
clawme --scopes calendar:readwrite gog users events insert ...
```

Available scope keys:

| Key | Access |
|-----|--------|
| `calendar:readonly` | Read calendar events |
| `calendar:readwrite` | Read and write calendar events |
| `drive:readonly` | Read Drive files |
| `drive:readwrite` | Read and write Drive files |
| `docs:readonly` | Read Google Docs |
| `docs:readwrite` | Read and write Google Docs |
| `sheets:readonly` | Read Google Sheets |
| `sheets:readwrite` | Read and write Google Sheets |
| `gmail:readonly` | Read Gmail messages |
| `gmail:readwrite` | Send and modify Gmail messages |

### List all connected integrations

```bash
clawme list
clawme list --json
```

## Exit codes and how to respond

| Code | Meaning | What to tell the user |
|------|---------|-----------------------|
| `0` | Success | Proceed |
| `1` | Error | "There was a problem connecting to the integration service. Please try again." |
| `2` | Needs reconnect | Relay the URL from stderr: "Your Google connection has expired. Please reconnect here: <url>" |
| `3` | Not connected / missing scopes | Relay the URL from stderr: "Google isn't connected yet. You can set it up here: <url>" |

On exit codes 2 and 3, the URL in stderr already contains the right scopes so the user
just clicks it and the authorization starts automatically.

## Example — read Gmail messages via gog

```bash
clawme --scopes gmail:readonly gog users messages list --user-id=me --max-results=5 --format=json
```

The `gog` binary receives a valid, fresh credential automatically — no token management needed.

## Gmail watch — real-time push notifications

Use `gmail-watch` when the user wants the agent to react to incoming emails automatically
(e.g. "notify me when I get an invoice", "process new support emails").

The platform manages Pub/Sub infrastructure. When a new email arrives, the platform pushes
a notification directly to this instance's `/hooks/clawme-gmail` endpoint. The transform
script deduplicates, enriches the email via `gog`, and forwards it to `/hooks/gmail`
for agent processing — **no polling required**.

### Commands

#### Start watching (run once)

```bash
clawme gmail-watch start
```

This:
1. Registers a push-notification watch with the platform for your connected Google account
2. Writes `~/.openclaw/hooks/transforms/clawme-gmail.js` (dedup + enrich transform)
3. Enables `hooks` in `~/.openclaw/config.yml` if not already set
4. Saves the webhook authentication token to `~/.openclaw/gmail-webhook-token`

The watch lasts ~7 days. The platform auto-renews it in the background.

#### Check active watches

```bash
clawme gmail-watch status
clawme gmail-watch status --json
```

#### Stop watching

```bash
clawme gmail-watch stop                   # stop all watches
clawme gmail-watch stop --email a@b.com  # stop one specific email
```

If no watches remain, the transform script and token file are cleaned up automatically.

### How the push flow works

```
Gmail → GCP Pub/Sub → Platform /webhooks/gmail
  → looks up instances watching that email
  → POST http://{instance-name}:{port}/hooks/clawme-gmail
      { email_address, history_id }
  → clawme-gmail.js transform:
      1. validate webhook_token
      2. dedup (skip if history_id already processed)
      3. clawme --scopes gmail:readonly gog users history list --start-history-id=...
      4. clawme --scopes gmail:readonly gog users messages get --id=... --format=json
      5. POST http://localhost:{port}/hooks/gmail  ← agent event
```

### Exit codes for gmail-watch

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success | Proceed |
| `1` | Error | Relay error to user |
| `2` | Google token revoked | Relay reconnect URL from stderr to user |

## Notes

- Tokens are automatically refreshed by the platform — `clawme gog` always provides a valid credential.
- If the user revokes access in their Google account, you will get exit code 2. Always relay the reconnect URL.
- `clawme` is different from `platform.py`: `clawme` is the high-level integration runner;
  `platform.py` is the low-level raw API tool. Prefer `clawme` for all integration work.
- One instance can watch multiple email addresses. One email can be watched by multiple instances.
