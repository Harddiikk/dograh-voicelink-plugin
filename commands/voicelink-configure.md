---
description: Guide configuring VoiceLink after install — fill the Settings → Telephony card (API base, credentials, DID), bind DIDs to inbound workflows, and paste the single WSS URL into the VoiceLink portal.
---

# /voicelink-configure

Configure a VoiceLink-enabled Dograh. Assumes the overlay is installed and verified
(`/voicelink-install`, `/voicelink-verify`). Follow `references/telephony-card-guide.md`
and `references/single-wss-url.md`.

## 1. The single WSS URL (print it for the user)

Derive from their public origin and show both forms:
```
Inbound  (paste in VoiceLink portal):  wss://<host>/api/v1/telephony/ws
Outbound (Dograh sends automatically): wss://<host>/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}
```
`scripts/verify.sh <base-url>` prints these exactly. The host = `BACKEND_API_ENDPOINT`
with `https`→`wss`. **This is the only URL VoiceLink needs — one URL, both directions.**

## 2. Settings → Telephony card (in the Dograh UI)

Walk the user through **Settings → Telephony → Add telephony configuration → VoiceLink**:

| Field | Value | Note |
|---|---|---|
| **API Base URL** | `https://app.voicelink.co.in/api` (or theirs) | VoiceLink REST API — **NOT** the WSS URL |
| **Username + Password** | VoiceLink login | Lets tokens auto-refresh on 401 |
| **Bearer Token** | (alternative to user/pass) | Static; no auto-refresh |
| **DID Number** | e.g. `919484959244` | Registered form; outbound caller id |
| **Phone Numbers** | (not shown on the add form) | Manage DIDs on the config detail page instead |

Save (requires either bearer_token OR username+password). Then on the config detail page,
add each DID and **bind it to an inbound workflow** — this is what makes inbound routing
work (it creates the `telephony_phone_numbers` row keyed on the normalized DID).

## 3. VoiceLink portal

In VoiceLink's own panel, set the inbound bot/stream URL to the **bare**
`wss://<host>/api/v1/telephony/ws`. Outbound needs no portal config.

## 4. Sanity

- Outbound `customer_number` must be a **bare 10-digit** local number (no country code),
  or the carrier rejects with Q.850 cause 38.
- Place a test outbound call and confirm the logged `add_lead` `websocket_url`; then a
  test inbound call and read the logged raw `start` frame (`/voicelink-debug` if routing
  misses). Confirm before declaring it done.
