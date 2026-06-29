---
description: Install the VoiceLink telephony provider into a Dograh instance from scratch (source checkout or Docker overlay). Copies the provider package, applies the three wiring edits, and guides BACKEND_API_ENDPOINT + verification.
---

# /voicelink-install

Install VoiceLink into Dograh end-to-end using the bundled, proven provider overlay.

Use the **dograh-voicelink-integration** skill as the source of truth. Work through its
workflow as a todo list. `${CLAUDE_PLUGIN_ROOT}` is this plugin's install directory.

## Preflight

1. **Find the Dograh root** — the directory containing
   `api/services/telephony/providers/__init__.py`. If you can't find it, ask the user
   where their Dograh code lives (or which container/image runs the api).
2. **Detect the mode** — source checkout vs Docker image (see the skill, Step 1).
   If ambiguous, ask which matches the *running* api.
3. **Confirm before writing** — run the overlay in `--dry-run` first and show the user
   the planned changes. Modifying their Dograh source/image is a real change; get a nod.

## Execute

- **Source mode:**
  ```bash
  python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_overlay.py" --dograh-root <ROOT> --with-tests --dry-run
  python "${CLAUDE_PLUGIN_ROOT}/scripts/apply_overlay.py" --dograh-root <ROOT> --with-tests
  ```
- **Docker-overlay mode:**
  ```bash
  "${CLAUDE_PLUGIN_ROOT}/assets/docker/build-and-deploy.sh" --tag <registry>/dograh-api:voicelink
  ```
  Then point the compose `api` service at the new image.

If any edit reports `✗`, apply the matching `assets/patches/*.md` by hand, then re-run
with `--edits-only` to confirm. Nothing is left half-written.

## Then

1. Set `BACKEND_API_ENDPOINT` to a public `https://` origin (skill Step 3).
2. Ensure the reverse proxy passes the WebSocket upgrade on `/api/v1/telephony/ws`.
3. Restart the api.
4. Run `/voicelink-verify https://<api-domain>` and confirm the `101` upgrade probe.
5. Hand off to `/voicelink-configure` for the telephony card + the VoiceLink portal URL.

## The deliverable of setup: the WSS URL → the VoiceLink client

When setup is complete, the **single WSS URL is the thing you hand the user to paste into
their VoiceLink portal (the client)**. The overlay script and `verify.sh` both print it.
Close the install by stating it explicitly, e.g.:

```
✅ VoiceLink is installed. Paste this into your VoiceLink portal (the client)
   as the inbound bot/stream URL — it serves BOTH inbound and outbound calls:

       wss://<your-api-host>/api/v1/telephony/ws
```

(The host = `BACKEND_API_ENDPOINT` with `https`→`wss`. Outbound appends the run ids
automatically per call — the user only ever pastes the bare URL above.)

Report exactly what changed, what verified, and the WSS URL the user must paste into
VoiceLink (plus: add the telephony config, bind DIDs). Never claim success without the
verify output.
