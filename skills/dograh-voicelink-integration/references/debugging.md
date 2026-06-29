# Debugging VoiceLink ↔ Dograh

Systematic, evidence-first. Run a check, read the output, localize the failing layer,
then fix. The provider logs are run-scoped (`[run {workflow_run_id}]`) via loguru.

## Triage ladder (stop at the first failing rung)

### 1. Is the provider registered and routed?
```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh" https://api.your-domain.com
```
- health 200 + `101` on `/api/v1/telephony/ws` → registered and mounted. ✅
- **404 on /ws** → the voicelink routes didn't mount: overlay not applied, or the api
  wasn't rebuilt/restarted after applying. Re-run `/voicelink-install`, restart api.
- With a bearer token, confirm `voicelink` in
  `GET /api/v1/organizations/telephony/providers/metadata`.

### 2. Is the WSS URL real and reachable?
- `BACKEND_API_ENDPOINT` must be a public `https://` origin → derived `wss://`. The
  **localhost trap:** the default `http://localhost:8000` yields `ws://localhost:8000`,
  which VoiceLink cannot reach. Set the public https origin and restart.
- Reverse proxy must pass the **WebSocket upgrade** on `/api/v1/telephony/ws` (Caddy:
  automatic; nginx: set `Upgrade`/`Connection` headers — see `env-and-deploy.md`). A
  probe that connects but never returns `101` usually means the proxy is the culprit.
- The api logs the chosen HTTP/WS URLs at DEBUG (`get_backend_endpoints`).

### 3. Outbound call fails at dial
Read the api log for the `add_lead` payload (logged at INFO — did / customer /
`websocket_url` / `webhook_url`).
- **`customer_number` must be bare 10-digit** local (no `91`). A 91-prefixed customer
  number is rejected by the carrier with **Q.850 cause 38**. (Normalization strips a
  12-digit `91…` and an 11-digit `0…`; a literal 10-digit number is left as-is.)
- `did_number` keeps its registered (91-prefixed) form — it's the caller id.
- **Auth:** config needs `bearer_token` OR `username`+`password`. With **bearer-only**, an
  expired token returns 401 and there's **no re-login** (no creds) — switch to
  username/password so the one-shot 401 re-login retry can refresh the token. The password
  is never logged.
- Non-2xx from VoiceLink → `HTTPException` (422 on provider error / 502 otherwise) with
  the upstream status+body logged.

### 4. Inbound call doesn't route
Read the logged **raw `start` frame**: `VoiceLink INBOUND start frame (raw): …`, then the
parsed `to`/`from`/`stream_sid`/`call_sid`.
- A `telephony_phone_numbers` row must exist for the **normalized called DID**, with
  `is_active=true` and an `inbound_workflow_id`. Missing/inactive → close `4404`.
- **Inbound `start`-frame field names are "unconfirmed upstream"** — the handler
  `pick()`s the DID across several spellings (`to`/`to_number`/`called`/`did`, etc.). If
  the real frame uses a key it doesn't try, the DID comes back empty. Compare the logged
  frame to `providers/voicelink/routes.py`, add the key to the `pick(...)` list, and add a
  regression test. This is the most likely first-inbound-call gotcha.

### 5. Audio problems
- Codec is **G.711 A-law @ 8 kHz** (`audio/alaw`), **not mu-law**. A mu-law assumption
  anywhere produces noise.
- **Quiet / VAD not triggering on inbound** (common on Indian carriers): raise
  `VOICELINK_INBOUND_GAIN` (e.g. `1.5`–`3.0`). It's read live in the serializer — no
  rebuild. The serializer logs inbound RMS ~once/second (every 50 frames).
- Barge-in / interruption sends `{"event":"clear","stream_sid":…}` to VoiceLink.

## WS close codes

| Code | Meaning |
|---|---|
| `4400` | missing/expected `start` frame, no `stream_sid`, or no DID found in the frame |
| `4404` | DID not configured / no `inbound_workflow_id` / referenced workflow missing |
| `4409` | run not initialized (outbound run wasn't created) |
| `1011` | internal error — read the traceback in the api logs |
| (clean `WebSocketDisconnect` before `start`) | treated as expected end-of-call, logged at info |

## Logs to watch
```bash
docker compose logs -f api      # call lifecycle, add_lead payload, start frames, RMS
docker compose logs -f caddy    # TLS issuance + WS-upgrade problems
```

## Known gaps (by design — not bugs, but good to know)
- **Webhooks are unsigned.** `verify_webhook_signature` / `verify_inbound_signature` /
  `validate_account_id` are no-ops; the **DID match is the only inbound auth boundary**.
  Hardening: add a shared-secret/HMAC check on `/voicelink/events`.
- **No per-call status/cost; no transfers.** `get_call_status` → `"unknown"`,
  `get_call_cost` → zeros, `transfer_call` → `NotImplementedError`,
  `supports_transfers()` → `False`. Wire these if VoiceLink exposes the APIs.
- **`_config_loader` omits `client_id`** even though config + UI metadata define it — so
  `client_id` never reaches `add_lead`. If you need it, add `"client_id":
  value.get("client_id")` to `_config_loader` in `providers/voicelink/__init__.py`.
- **Recording URL spelling guessed** — read from `recordingUrl` or `recording_url`
  defensively; confirm against a real event.
- **No synthetic end-to-end WS healthcheck** beyond the `verify.sh` upgrade probe;
  full confidence needs a real call.

## Correctness contract
The bundled `assets/tests/voicelink/` pins the behavior. After install (`--with-tests`),
run them in the Dograh test env. They mock these seams — keep import paths intact:
`_api_request` / `_send_request` / `_login`, `get_backend_endpoints`, `db_client`,
`get_telephony_provider_for_run`, `_process_status_update`.
