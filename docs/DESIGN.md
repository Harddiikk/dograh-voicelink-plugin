# Design — Dograh VoiceLink Plugin

The reasoning behind the plugin: what it builds, why it's shaped this way, and how it was
verified. Pair this with [`integration-map.md`](integration-map.md) (the reproduction-grade
map of the underlying integration).

## Goal

A self-contained Claude Code plugin that takes a stock Dograh from **zero to working
VoiceLink calling** (inbound + outbound) with one command, reproducing the exact
integration already proven on two deployments (GPC and the orders "voice-engine" fork) —
without dragging along the parts of that fork that don't generalize.

## Background: how VoiceLink fits into Dograh

VoiceLink is an ordinary Dograh **telephony provider** (same abstraction as Twilio, Plivo,
Telnyx, Vonage, Vobiz, Cloudonix, ARI). Dograh's provider system is **registry-driven**:
importing a provider package runs `register(SPEC)`, routes auto-mount by convention, and
the factory / audio_config / run_pipeline are all driven off the registry. Adding a
provider is therefore a small, bounded change — ideal for a copy-paste overlay.

The canonical id is the string `voicelink` (`WorkflowRunMode.VOICELINK`). Media rides a
single WebSocket using **G.711 A-law @ 8 kHz** in a Twilio-Media-Streams-style JSON
envelope; outbound dialing is a REST `add_lead` to VoiceLink's cloud; lifecycle events are
unsigned webhook POSTs.

## Key decision: provider-only overlay, not the full fork

The orders `voice-engine` fork contains the working provider **plus** a white-label
reseller-KYC/SaaS layer and two Alembic migrations. Installing the whole fork image onto a
stock Dograh breaks: it carries unmerged Alembic heads and KYC/SaaS code that won't cleanly
migrate an existing DB.

The GPC deployment already proved the right answer: a **provider-only overlay** on the
stock `dograhai/dograh-api:latest` — copy the `voicelink/` package, add the `VOICELINK`
enum member, add the one registration import. The only stock-missing symbol the provider
needs is `WorkflowRunMode.VOICELINK`. No migration is required because `provider` is a
plain string column.

So the plugin packages **exactly that minimal overlay**: one package copy + three edits
(the enum member, the registration import, and the `telephony_config.py` schema wiring —
the last is what lets the telephony card *save* a VoiceLink config). `BACKEND_API_ENDPOINT`
is a pre-existing env var, not a code edit. KYC/SaaS is explicitly out of scope (documented
as optional in the references).

## The single WSS URL — and the premise correction

VoiceLink uses **one WSS URL for both directions**: `wss://<host>/api/v1/telephony/ws`
(inbound = bare; outbound = `…/ws/{workflow_id}/{user_id}/{workflow_run_id}`, sent inline
per call). This is true and is the plugin's headline.

The important correction baked into the design: that WSS URL is **not** a field in the
Dograh telephony card. It is **derived** from `BACKEND_API_ENDPOINT` (`https`→`wss`) and
pasted into the **VoiceLink portal**. The card's only URL-shaped placeholder is the **API
Base URL** (`https://app.voicelink.co.in/api`) — VoiceLink's REST API, the opposite
direction. The plugin ships *both* guides and is explicit about which URL goes where, so it
doesn't send anyone hunting for a WSS field that doesn't exist.

## Plugin architecture

- **`scripts/apply_overlay.py`** — the engine. Stdlib-only, idempotent, auto-detects the
  Dograh root. Copies the provider package and applies three edits (enum, registration,
  and `telephony_config.py` schema = union + response field + `__all__`). Each edit checks
  for prior presence;
  anchors are line-based to survive parens-in-comments. On a missing anchor it fails
  loudly and points at the matching `assets/patches/*.md`. `--compile-check` py_compiles
  the result (used in the Docker build).
- **`assets/provider/voicelink/`** — the 6 real provider files, bundled verbatim (true
  copy-paste; already running on GPC + orders).
- **`assets/docker/`** — `Dockerfile.voicelink-overlay` (FROM the stock image + overlay +
  compile-check) and `build-and-deploy.sh`.
- **`assets/patches/`** — exact manual-fallback diffs for the three edits.
- **`assets/tests/voicelink/`** — the 3 real pytest files (the correctness contract).
- **`scripts/verify.sh`** — read-only verifier: health, a WS-upgrade probe on the bare
  `/ws` (a `101` proves the routes mounted, no auth needed), and providers metadata.
- **`skills/dograh-voicelink-integration/`** — the playbook Claude follows, with five
  references (single-wss-url, telephony-card-guide, env-and-deploy, debugging,
  integration-map).
- **`commands/`** — thin `/voicelink-install · -configure · -verify · -debug` orchestrators.

## Both install modes (auto-detect)

- **Source checkout** — apply edits directly to the working tree; the user builds/runs.
- **Docker overlay** — build a derived image with the provider baked in; point compose at
  it. Never build on a tiny VPS; build elsewhere and pull.

The script auto-detects the root by looking for `api/services/telephony/providers/__init__.py`
under common roots and by walking up from the CWD; `--dograh-root` overrides.

## Verification

The overlay engine ships with a self-contained regression suite,
`tests/test_apply_overlay.py` (`python tests/test_apply_overlay.py`, no Dograh checkout
needed). It synthesizes Dograh-shaped fixtures and asserts via Python AST that after
applying: `WorkflowRunMode.VOICELINK == "voicelink"`; `voicelink` is a real imported name
(not a comment); the response field is a real `AnnAssign` (not absorbed into the class
docstring); the config import + union member + `__all__` exports are all present (and the
*original* exports survive intact); and a second run is a no-op (idempotent). It runs the
**standard**, **no-trailing-comma**, and **tab-indented** layouts, plus a
**rollback-on-compile-failure** case (a pre-existing syntax error → the edited files are
restored and the run exits non-zero).

Development against a stripped-down copy of the real fork caught and fixed three real bugs
before shipping: a `)`-in-comment mis-insertion, the response field being absorbed into the
class docstring, and the no-trailing-comma corruption (adjacent tokens / silent `__all__`
string-concat) surfaced by the pre-publish adversarial review. Each is now a regression
case in the suite.

## Known gaps (carried faithfully, documented, not silently "fixed")

- Webhooks are **unsigned** — the DID match is the only inbound auth boundary.
- No per-call status/cost; `transfer_call` is `NotImplementedError`.
- `_config_loader` omits `client_id`.
- Inbound `start`-frame field names are "unconfirmed upstream" — the handler `pick()`s
  across spellings and logs the raw frame; confirm against a real inbound call.

These are documented in `references/debugging.md` as optional hardening so the plugin
matches reality rather than papering over it.

## Future-proofing

- The overlay is anchor-based with idempotency + a manual-patch fallback, so it survives
  Dograh refactors better than a blind patch; if an anchor moves, it fails loudly with a
  pointer instead of corrupting a file.
- The bundled provider files can be refreshed from upstream by re-copying into
  `assets/provider/voicelink/` and bumping the plugin version.
- Because registration is registry-driven, future Dograh provider-system changes are
  unlikely to need more than the same package-copy + three edits.
