---
description: Verify a VoiceLink ↔ Dograh install — API health, the single WSS endpoint upgrade probe, and (with a token) that voicelink appears in the telephony providers metadata. Read-only.
---

# /voicelink-verify

Run the read-only verifier and interpret the result. Usage the user gives you a base URL
(their public api origin); default `http://localhost:8000`.

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh" <base-url> [--token <bearer>]
```

## What it checks

1. **Health** — `GET <base>/api/v1/health` → 200.
2. **Single WSS endpoint** — WS upgrade probe on the bare `/api/v1/telephony/ws`. A
   `101 Switching Protocols` proves the voicelink package registered and its routes
   auto-mounted (the bare `/ws` is VoiceLink-specific). No auth needed.
3. **Providers metadata** — with `--token`, confirms `voicelink` is in the Settings →
   Telephony dropdown.

It also prints the inbound/outbound WSS URLs and the events webhook.

## Interpreting results

- **101 on /ws** → overlay is live. ✅
- **404 on /ws** → routes not mounted: overlay not applied, or the api wasn't rebuilt/
   restarted. Re-run `/voicelink-install` and restart.
- **No 101 / 426 / connection error** → the route exists but the **reverse proxy isn't
   passing the WebSocket upgrade**, or `BACKEND_API_ENDPOINT` isn't a public https origin.
   See `references/env-and-deploy.md` (Caddy/nginx WS config).
- **ws:// printed instead of wss://** → base is http; set `BACKEND_API_ENDPOINT` to https.

If anything fails, go to `/voicelink-debug`. Report the actual verifier output — don't
assert success without it.
