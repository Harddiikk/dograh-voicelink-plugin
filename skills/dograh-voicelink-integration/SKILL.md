---
name: dograh-voicelink-integration
description: Integrate the VoiceLink telephony provider into a Dograh instance from scratch — copy the provider package, apply the three wiring edits (source checkout OR Docker overlay), configure the single inbound+outbound WSS URL via BACKEND_API_ENDPOINT, set up the Settings → Telephony card, and verify end-to-end. Use whenever the user wants to add, configure, debug, or verify VoiceLink calling on Dograh.
metadata:
  type: reference
  keywords: [dograh, voicelink, telephony, wss, websocket, inbound, outbound, pipecat, overlay]
---

# Dograh ↔ VoiceLink integration

This skill makes a stock Dograh speak VoiceLink, both **inbound** and **outbound**,
using the proven non-breaking provider-overlay approach (the same one running on the
GPC and orders deployments). The bundled `assets/provider/voicelink/` package is the
real, tested implementation — this skill just installs and wires it correctly.

`${CLAUDE_PLUGIN_ROOT}` is the plugin's install directory (it contains `scripts/`,
`assets/`, `skills/`). All paths below are relative to it.

## The one mental model you must hold: the single WSS URL

VoiceLink uses **ONE WebSocket URL for both directions**. It is **derived**, not pasted:

```
wss://<your-public-host>/api/v1/telephony/ws
```

- **Inbound** calls connect to that **bare** URL. Dograh reads the `start` frame,
  routes by the called DID, creates a run, and runs the pipeline.
- **Outbound** calls use the **same** URL with the run identity appended:
  `…/ws/{workflow_id}/{user_id}/{workflow_run_id}`. Dograh sends this to VoiceLink
  automatically inside the `add_lead` request — nothing to configure per call.

The host comes from the env var **`BACKEND_API_ENDPOINT`** (`https://` → `wss://`).
There is **no WSS field in the Dograh telephony card**. The card's "API Base URL"
placeholder (`https://app.voicelink.co.in/api`) is VoiceLink's REST API, a *different*
thing. See `references/single-wss-url.md` — internalize this before configuring, or you
will hunt for a field that does not exist.

## Workflow

Create one todo per step and work through them in order.

### Step 1 — Locate the Dograh target and pick a mode

Find the Dograh code root: the directory whose `api/services/telephony/providers/__init__.py`
exists. Then determine how this Dograh runs:

- **Source checkout** (a working tree / fork you build or run directly) → **Source mode**.
- **Docker image** (the api runs from `dograhai/dograh-api:latest` or a derived image via
  docker-compose) → **Docker-overlay mode**.

If both are plausible (a checkout that's also containerized), ask the user which one is
authoritative for their deployment. When unsure, prefer the mode that matches how the
**running** api gets its code.

### Step 2 — Apply the overlay

**Source mode:**
```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_overlay.py" --dograh-root <ROOT> --with-tests --dry-run   # preview
python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_overlay.py" --dograh-root <ROOT> --with-tests             # apply
```
The script is idempotent. It copies the provider package and applies the three edits:
`WorkflowRunMode.VOICELINK`, the registration import, and the `telephony_config.py`
schema union. If any edit reports `✗`, open the matching file in `assets/patches/` and
apply it by hand (the layout is unusual), then re-run with `--edits-only` to confirm.

**Docker-overlay mode:**
```bash
"${CLAUDE_PLUGIN_ROOT}/assets/docker/build-and-deploy.sh" \
    --base dograhai/dograh-api:latest \
    --tag  <your-registry>/dograh-api:voicelink
```
This builds a derived image with the provider baked in and a build-time compile-check.
Then point the compose `api` service at `<your-registry>/dograh-api:voicelink`. Never
build on a tiny VPS (2GB can't build) — build elsewhere, push, and `docker compose pull`.
See `references/env-and-deploy.md`.

> Do **not** use the full `voice-engine` fork image for GPC-style deployments — it carries
> KYC/SaaS code and unmerged Alembic migrations that won't cleanly migrate a stock DB. The
> overlay needs no migrations (the `provider` column is a plain string).

### Step 3 — Set the single WSS host (`BACKEND_API_ENDPOINT`)

In the api environment (e.g. `.env.api`), set it to your **public `https://`** origin:
```
BACKEND_API_ENDPOINT=https://api.your-domain.com
```
- Must be `https://` (so the derived media URL is `wss://`, not `ws://`) and publicly
  reachable, or VoiceLink cannot stream audio back.
- The reverse proxy (Caddy/nginx) must pass the **WebSocket upgrade** through to the api
  on `/api/v1/telephony/ws`. Caddy's `reverse_proxy` does this natively.
- No TURN/coturn is needed — VoiceLink media is server-to-server WSS.

### Step 4 — (Re)build / restart and verify

Restart the api so the new code/env loads (`docker compose up -d api`, or restart your
source process). Then:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh" https://api.your-domain.com
```
This checks `/api/v1/health`, probes the bare `/ws` for a `101` upgrade (proof the
voicelink routes mounted), and prints the exact WSS URL to paste into the VoiceLink
portal. Pass `--token <bearer>` to also confirm `voicelink` shows in the providers
metadata. Drive it via `/voicelink-verify`.

### Step 5 — Configure the Settings → Telephony card

In the Dograh UI, **Settings → Telephony → Add telephony configuration**, choose
**VoiceLink**, and fill in (these are the *real* card fields — none of them is the WSS URL):

| Field | What to enter |
|---|---|
| **API Base URL** | VoiceLink REST API base (placeholder `https://app.voicelink.co.in/api`) |
| **Username + Password** *or* **Bearer Token** | VoiceLink credentials (username/password lets tokens auto-refresh) |
| **DID Number** | Your DID in registered form, e.g. `919484959244` (used as outbound caller id) |

(The metadata also defines a "Phone Numbers" / `from_numbers` field, but the add-config
form does **not** render it — DIDs are managed separately, below.)

Save. Then on the config detail page, add DIDs and bind each to an **inbound workflow**
(this creates the `telephony_phone_numbers` row the inbound handler routes against).
Full walkthrough: `references/telephony-card-guide.md`.

### Step 6 — Point VoiceLink at the single WSS URL

In **VoiceLink's own portal/bot config**, set the inbound bot/stream URL to the **bare**:
```
wss://api.your-domain.com/api/v1/telephony/ws
```
Outbound needs no portal config — Dograh passes the templated URL inline per call.

### Step 7 — Test calls

- **Outbound:** launch a call; in the api logs confirm the `add_lead` payload's
  `websocket_url` and that VoiceLink connects back to `/ws/{…}`.
- **Inbound:** call your DID; read the logged raw `start` frame. Inbound field names are
  "unconfirmed upstream" — if routing misses, capture the frame and adjust (see
  `references/debugging.md`). Remember: outbound `customer_number` must be a **bare
  10-digit** local number, or the carrier rejects it (Q.850 cause 38).

## Correctness contract

The bundled `assets/tests/voicelink/` pins the behavior (A-law 8 kHz codec, `add_lead`
payload shape, computed WSS URL, 401 re-login retry, event→status map, the events route
never raising). Run them in the Dograh repo's test env after install (`--with-tests`
copies them in). See `references/integration-map.md` §7.

## Debugging

When a call fails, use `/voicelink-debug` and `references/debugging.md`. Fast triage:
`/api/v1/health` → `verify.sh` 101 probe → providers metadata → WS-upgrade in the proxy →
`BACKEND_API_ENDPOINT` is public https → DID row exists & is active. Known WS close codes:
`4400` (bad/missing start), `4404` (DID not configured), `4409` (run not initialized),
`1011` (internal).

## References

- `references/single-wss-url.md` — the one URL, both directions, where each piece goes
- `references/telephony-card-guide.md` — the UI card fields, placeholders, save flow, DIDs
- `references/env-and-deploy.md` — every env var, Caddy/nginx WS upgrade, no-TURN, memory
- `references/debugging.md` — close codes, logs, the localhost `ws://` trap, known gaps
- `references/integration-map.md` — the full reproduction-grade map of the integration
