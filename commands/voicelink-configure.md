---
description: Guide configuring VoiceLink after install — fill the Settings → Telephony card (credentials only), bind DIDs to inbound workflows, and paste the single WSS URL into the VoiceLink portal.
---

# /voicelink-configure

Configure a VoiceLink-enabled Dograh. Assumes the overlay is installed and verified
(`/voicelink-install`, `/voicelink-verify`). Follow `references/telephony-card-guide.md`
and `references/single-wss-url.md`.

## 1. The single WSS URL (print it for the user)

Derive from their public origin and show both forms:
```
Inbound  (paste in VoiceLink portal):  wss://<host>/api/v1/telephony/ws
Outbound (Dograh sends automatically): wss://<host>/api/v1/telephony/ws/{workflow_id}/{organization_id}/{workflow_run_id}
```
`scripts/verify.sh <base-url>` prints these exactly. The host = `BACKEND_API_ENDPOINT`
with `https`→`wss`. **This is the only URL VoiceLink needs — one URL, both directions.**

## 2. Settings → Telephony card (in the Dograh UI)

Walk the user through **Settings → Telephony → Add telephony configuration → VoiceLink**.
The card is **credentials only**:

| Field | Value | Note |
|---|---|---|
| **Username + Password** | VoiceLink login | Lets tokens auto-refresh on 401 |
| **Bearer Token** | (alternative to user/pass) | Static; no auto-refresh |

No API base or DID field: `api_base` defaults to `https://app.voicelink.co.in/api` in the
schema. DIDs are added as phone-number rows in the next step.

Save (requires either bearer_token OR username+password). Then on the config detail page,
**add each DID** — this is required for **both** directions:
- **Inbound:** bind the DID to an inbound workflow (creates the `telephony_phone_numbers`
  row the inbound handler routes against).
- **Outbound:** the telephony factory turns the active rows into the provider's
  `from_numbers`; the campaign dispatcher uses one as the caller id. VoiceLink's `add_lead`
  rejects a call with no `did_number`, so at least one active DID must exist.

## 3. VoiceLink portal

In VoiceLink's own panel, set the inbound bot/stream URL to the **bare**
`wss://<host>/api/v1/telephony/ws`. Outbound needs no portal config.

## 4. Sanity

- Outbound `customer_number` must be a **bare 10-digit** local number (no country code),
  or the carrier rejects with Q.850 cause 38.
- Place a test outbound call and confirm the logged `add_lead` `websocket_url`; then a
  test inbound call and read the logged raw `start` frame (`/voicelink-debug` if routing
  misses). Confirm before declaring it done.
