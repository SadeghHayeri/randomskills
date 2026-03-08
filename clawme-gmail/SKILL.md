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

### Read Gmail messages

Use `clawme gmail` to search and read emails directly via the platform OAuth token.
No separate credentials needed - it uses the connected Google account automatically.

```bash
clawme gmail search "<query>" [--max=N] [--json]
clawme gmail list [--max=N] [--json]
clawme gmail get <message-id> [--json]
```

Examples:

```bash
# Search inbox for recent emails
clawme gmail search "in:inbox" --max=5 --json

# Find unread emails
clawme gmail search "is:unread" --max=10 --json

# Get a specific message
clawme gmail get 19ccf256a06ae6db --json
```

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

## Example — read Gmail messages

```bash
clawme gmail search "in:inbox" --max=5 --json
```

The platform provides a fresh OAuth token automatically - no token management needed.

## Gmail watch — real-time push notifications

Use `gmail-watch` when the user wants the agent to react to incoming emails automatically
(e.g. "notify me when I get an invoice", "process new support emails").

The platform manages Pub/Sub infrastructure. When a new email arrives, the platform pushes
a notification directly to this instance's `/hooks/clawme-gmail` endpoint. The transform
fetches the email from the Gmail REST API and delivers it to the agent as a prompt — **no
polling required**.

**Prerequisites:** Google must be connected with `gmail:readwrite` scope.

### Commands

#### Start watching (run once)

```bash
clawme gmail-watch start
```

This:
1. Registers a push-notification watch with the platform for your connected Google account
2. Writes `~/.openclaw/hooks/transforms/clawme-gmail.js` (auth + dedup + email fetch transform)
3. Enables `hooks` in `~/.openclaw/config.yml` if not already set
4. Patches the hooks mapping in the CRD config via selfconfig

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

### How the push flow works

```
Gmail → GCP Pub/Sub → Platform
  → POST /hooks/clawme-gmail on this instance
      { history_id, email_address }
      Authorization: Bearer <OPENCLAW_HOOKS_TOKEN>
  → clawme-gmail.js transform:
      1. validate auth (OPENCLAW_HOOKS_TOKEN)
      2. dedup (skip if history_id already processed)
      3. GET /api/instance/integrations/google/token  (fresh OAuth token)
      4. GET /gmail/v1/users/me/history?startHistoryId=...&maxResults=1
      5. GET /gmail/v1/users/me/messages/{id}?format=full
      6. return { message: "<formatted email>" }  → agent handles it
```

The transform returns `null` (no agent triggered) when:
- Auth validation fails
- history_id was already processed (dedup)
- No new messages found for this history ID

### Exit codes for gmail-watch

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success | Proceed |
| `1` | Error | Relay error to user |
| `2` | Google token revoked | Relay reconnect URL from stderr to user |

## Notes

- `gmail:readwrite` scope is required for gmail-watch (readwrite is needed even for read-only watching).
- The transform authenticates webhook POSTs using `OPENCLAW_HOOKS_TOKEN` (env var set by the platform).
- Email fetch uses the platform API token endpoint, not `clawme gog` — no separate binary needed.
- `clawme` is different from `platform.py`: `clawme` is the high-level integration runner;
  `platform.py` is the low-level raw API tool. Prefer `clawme` for all integration work.
- One instance can watch multiple email addresses. One email can be watched by multiple instances.
