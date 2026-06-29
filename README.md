# Dograh VoiceLink Plugin

A **zero-to-hero Claude Code plugin** that adds the **VoiceLink** telephony provider to a
[Dograh](https://github.com/dograh-hq/dograh) voice-AI instance — from nothing to working
**outbound** calls plus an **implemented inbound** path — using the proven, non-breaking
provider-overlay approach already running on the GPC and orders deployments.

> Outbound is fully proven in production. Inbound is implemented and wired the same way,
> but VoiceLink's inbound `start`-frame field names are *unconfirmed upstream* until a real
> inbound call is captured — see
> [debugging.md](skills/dograh-voicelink-integration/references/debugging.md) §4.

Install the plugin, run `/voicelink-install`, and Claude Code sets everything up: it drops
in the real provider package, applies the three wiring edits, wires the single
inbound+outbound WSS URL, helps you configure the Settings → Telephony card, and verifies
the whole thing end-to-end with built-in debugging. It supports both a **source checkout**
and a **Docker image overlay** — the script auto-detects the Dograh *root*; you (or Claude)
pick the install *mode*.

> **The one fact to know up front:** VoiceLink uses **ONE WSS URL for both inbound and
> outbound** calls — `wss://<your-host>/api/v1/telephony/ws`. It is **derived** from the
> `BACKEND_API_ENDPOINT` env var and pasted into the **VoiceLink portal**, *not* into the
> Dograh telephony card. The card's "API Base URL" placeholder is VoiceLink's REST API, a
> different thing. See [the single-WSS-URL guide](skills/dograh-voicelink-integration/references/single-wss-url.md).

---

## Install into Claude Code

```bash
# add this repo as a marketplace, then install the plugin
/plugin marketplace add Harddiikk/dograh-voicelink-plugin
/plugin install dograh-voicelink-plugin@dograh-voicelink-plugin
```

(Or clone and add it as a local marketplace: `/plugin marketplace add /path/to/dograh-voicelink-plugin`.)

Restart Claude Code if prompted. You'll then have the `/voicelink-*` commands and the
`dograh-voicelink-integration` skill.

## Quickstart

From a Claude Code session that can reach your Dograh code and/or deployment:

```text
/voicelink-install        # detect Dograh, apply the overlay (source or Docker), set the WSS host
/voicelink-configure      # fill the telephony card + paste the WSS URL into the VoiceLink portal
/voicelink-verify https://api.your-domain.com   # health + 101 WSS probe + providers metadata
/voicelink-debug          # systematic troubleshooting if a call misbehaves
```

That's it. Outbound and inbound calls both ride the same single WSS URL (inbound pending a
real-call confirmation of the `start`-frame fields — see debugging.md §4).

## What it does, concretely

The overlay makes exactly these changes to a stock Dograh (nothing else — no KYC/SaaS, no
Alembic migrations, no fork baggage):

1. **Copies** the real provider package → `api/services/telephony/providers/voicelink/`
   (6 files: `__init__`, `config`, `provider`, `transport`, `serializers`, `routes`).
2. **Adds** `VOICELINK = "voicelink"` to `WorkflowRunMode` in `api/enums.py`.
3. **Registers** it with one line in `api/services/telephony/providers/__init__.py`
   (routes auto-mount; factory/audio_config/run_pipeline are registry-driven).
4. **Wires** the config schema into the discriminated union in
   `api/schemas/telephony_config.py` (so the telephony card can save VoiceLink).
5. *(optional)* copies the pytest suite → `api/tests/telephony/voicelink/`.

The automation (`scripts/apply_overlay.py`) is **idempotent**, auto-detects the Dograh
root, applies each edit only if missing, and ships a manual-patch fallback for unusual
layouts. The Docker path builds a derived image with a **build-time compile-check**.

## The two URLs (don't mix them up)

| URL | Direction | Goes where |
|---|---|---|
| `wss://<host>/api/v1/telephony/ws` | VoiceLink → Dograh (call audio, **in + out**) | **VoiceLink portal** — derived from `BACKEND_API_ENDPOINT` (`https`→`wss`) |
| `https://app.voicelink.co.in/api` | Dograh → VoiceLink (dial / login) | **Dograh telephony card** → "API Base URL" |

- **Inbound** connects to the bare `…/ws`; **outbound** uses `…/ws/{workflow_id}/{user_id}/{workflow_run_id}` (Dograh sends it automatically per call).
- Set `BACKEND_API_ENDPOINT` to a public **`https://`** origin, and make sure your reverse
  proxy passes the **WebSocket upgrade** on `/api/v1/telephony/ws`. No TURN needed.

## Repository layout

```
.claude-plugin/         plugin.json + marketplace.json (install metadata)
commands/               /voicelink-install · -configure · -verify · -debug
skills/dograh-voicelink-integration/
  SKILL.md              the end-to-end integration playbook Claude follows
  references/           single-wss-url · telephony-card-guide · env-and-deploy · debugging · integration-map
assets/
  provider/voicelink/   the 6 real provider files (bundled verbatim — copy-paste)
  tests/voicelink/       the 3 real pytest files (correctness contract)
  patches/               exact manual-fallback snippets for the 3 edits
  docker/                Dockerfile.voicelink-overlay + build-and-deploy.sh
scripts/
  apply_overlay.py      idempotent overlay engine (copy + 3 edits + compile-check + rollback)
  verify.sh             health + WSS upgrade probe + providers metadata
tests/
  test_apply_overlay.py self-contained regression suite for the overlay engine
docs/                   DESIGN.md + the full integration-map.md
```

## Manual install (without Claude Code)

Everything Claude does is just these scripts — you can run them directly:

```bash
# Source checkout:
python scripts/apply_overlay.py --dograh-root /path/to/dograh --with-tests
# then set BACKEND_API_ENDPOINT=https://api.your-domain.com and restart the api.

# Docker overlay:
assets/docker/build-and-deploy.sh --tag <registry>/dograh-api:voicelink
# point your compose 'api' service at that image, set BACKEND_API_ENDPOINT, redeploy.

# Verify:
bash scripts/verify.sh https://api.your-domain.com
```

## Requirements

- A Dograh instance you can modify or rebuild (source checkout, or Docker via
  `dograhai/dograh-api:latest` / a derived image).
- Python 3 (stdlib only) to run the overlay; `docker` for the overlay-image path.
- A public `https://` origin for the api with WebSocket upgrades proxied through.
- VoiceLink account credentials and at least one DID.

## How it was built

Reverse-engineered from the real, tested VoiceLink integration in the `voice-engine`
Dograh fork and the GPC overlay deployment, then verified. The overlay engine ships with a
self-contained regression suite — `python tests/test_apply_overlay.py` synthesizes
Dograh-shaped fixtures (standard, no-trailing-comma, tab-indented) and asserts correct
AST-level wiring, idempotency, and rollback-on-compile-failure. See
[`docs/DESIGN.md`](docs/DESIGN.md) and [`docs/integration-map.md`](docs/integration-map.md).

## License

MIT — see [LICENSE](LICENSE).
