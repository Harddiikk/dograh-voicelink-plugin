---
description: Troubleshoot a VoiceLink ↔ Dograh call problem systematically — registration, the single WSS URL, reverse-proxy upgrade, DID routing, number formatting, and the WS close codes.
---

# /voicelink-debug

Systematically diagnose VoiceLink calling on Dograh. Be evidence-driven: run a check,
read the actual output, then decide. Full detail in `references/debugging.md`.

## Triage order (stop at the first failing layer)

1. **Is the provider registered?**
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh" <base-url>` → expect health 200 and a
   `101` on `/api/v1/telephony/ws`. 404 = overlay not applied / api not restarted.
2. **Is the WSS URL real and reachable?**
   `BACKEND_API_ENDPOINT` must be a public `https://` origin → derived `wss://`. A
   `ws://` (localhost) URL means VoiceLink can't connect. Confirm the reverse proxy
   (Caddy/nginx) passes the **WebSocket upgrade** on `/api/v1/telephony/ws`.
3. **Outbound fails immediately?**
   Read the api logs for the `add_lead` payload. `customer_number` must be **bare
   10-digit** (carrier rejects 91-prefixed customer numbers, Q.850 cause 38). Check the
   `did_number` is in registered form and credentials validate (`bearer_token` OR
   `username`+`password`). A 401 with bearer-only config means the token expired and
   there's no username/password to re-login.
4. **Inbound not routing?**
   Read the logged raw `start` frame (`VoiceLink INBOUND start frame (raw): …`). Confirm
   a `telephony_phone_numbers` row exists for the normalized called DID, `is_active=true`,
   with an `inbound_workflow_id`. Inbound `start`-frame field names are "unconfirmed
   upstream" — if the DID isn't being picked up, compare the logged frame to what the
   handler reads and adjust `providers/voicelink/routes.py` (`pick(...)`), then add a
   regression test.
5. **Audio is silent / one-way / robotic?**
   Codec is **G.711 A-law @ 8 kHz** (not mu-law). For quiet inbound audio (common on
   Indian carriers starving VAD/ASR), raise `VOICELINK_INBOUND_GAIN` (live-tunable, no
   rebuild). Check the serializer RMS log (~1/sec).

## WS close codes (what the server sent and why)

| Code | Meaning | Fix |
|---|---|---|
| `4400` | missing/expected `start`, no `stream_sid`, or no DID in frame | VoiceLink sent an unexpected frame; capture & compare |
| `4404` | DID not configured / no `inbound_workflow_id` / workflow missing | add the telephony_phone_numbers row + bind a workflow |
| `4409` | run not initialized | outbound run wasn't created; check `add_lead` flow |
| `1011` | internal error | read the traceback in api logs |

## Known gaps (by design, not bugs)

- Webhooks are **unsigned** — the DID match is the only inbound authorization boundary.
- `get_call_status` → `"unknown"`, `get_call_cost` → zeros, `transfer_call` →
  `NotImplementedError` (no per-call status/cost/transfer API wired).
- `_config_loader` does not pass through `client_id` (optional hardening; see
  `references/debugging.md`).

Report findings layer by layer. Don't propose a fix before you've read the log/probe that
localizes the failure (systematic debugging).
