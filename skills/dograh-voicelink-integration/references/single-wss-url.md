# The single WSS URL — one URL, both directions

VoiceLink uses **one media WebSocket** for **both inbound and outbound** calls. Same
protocol, same codec (**G.711 A-law @ 8 kHz**, base64 in a Twilio-Media-Streams-style
JSON envelope), same serializer, same pipeline entrypoint
(`run_pipeline_telephony(provider_name="voicelink")`). This is the headline fact, and
it's true in the real code — they converge on the exact same transport factory.

## The URL

```
wss://<your-public-host>/api/v1/telephony/ws
```

The two directions differ in only two ways:

| | Inbound | Outbound |
|---|---|---|
| **Path** | bare `…/ws` | `…/ws/{workflow_id}/{user_id}/{workflow_run_id}` |
| **Run** | created on the fly: read `start` frame → route by called DID → create run | already created at dial time; identity is in the path |
| **Served by** | `providers/voicelink/routes.py` `@router.websocket("/ws")` | the generic `api/routes/telephony.py` `/ws/{…}` route |

Outbound is automatic: when Dograh dials (`POST {api_base}/v1/add_lead`), it builds the
templated `websocket_url` and the `webhook_url` and ships them **inline** in the request.
You never configure the outbound URL by hand.

## Where the host comes from — `BACKEND_API_ENDPOINT`

The `<your-public-host>` part is **not stored anywhere** and is **not a config field**.
It is derived at runtime from the env var `BACKEND_API_ENDPOINT` by swapping the scheme:

```python
# api/utils/common.py :: get_backend_endpoints()
ws_scheme = {"http": "ws", "https": "wss"}[scheme]
ws_url = BACKEND_API_ENDPOINT.rstrip("/").replace(scheme, ws_scheme, 1)
# then: f"{ws_url}/api/v1/telephony/ws" (+ "/{workflow_id}/{user_id}/{workflow_run_id}" for outbound)
```

So **the single WSS URL = `BACKEND_API_ENDPOINT` (https→wss) + `/api/v1/telephony/ws`.**

- `BACKEND_API_ENDPOINT=https://api.example.com` → `wss://api.example.com/api/v1/telephony/ws` ✅
- `BACKEND_API_ENDPOINT=http://localhost:8000` (the default) → `ws://localhost:8000/...`
  — plain `ws://`, not reachable by VoiceLink. **Set a public https origin.**
  (If left at localhost, Dograh falls back to a cloudflared tunnel URL — fine for local
  dev, not for production.)

## ⚠️ The placeholder confusion (read this)

There is **no field in the Dograh telephony card where you paste the WSS URL.** People
look for one because the card *does* have a URL-shaped placeholder — but that is the
**API Base URL** (`https://app.voicelink.co.in/api`), which is VoiceLink's **REST API**
(the direction *Dograh → VoiceLink*, for `add_lead`/login). It is **not** the media WSS
URL (the direction *VoiceLink → Dograh*, for audio). Do not paste the WSS URL there.

Two different URLs, two different places:

| URL | Direction | Where it goes |
|---|---|---|
| `wss://<host>/api/v1/telephony/ws` | VoiceLink → Dograh (audio) | **VoiceLink portal** (inbound bot/stream URL); set `BACKEND_API_ENDPOINT` so it's derived |
| `https://app.voicelink.co.in/api` | Dograh → VoiceLink (dial/login) | **Dograh telephony card** → "API Base URL" field |

## End-to-end data flow

1. **Deployer** sets `BACKEND_API_ENDPOINT=https://api.example.com` in the api env.
2. **Operator** pastes `wss://api.example.com/api/v1/telephony/ws` into the VoiceLink
   portal as the inbound bot URL (so incoming calls stream there).
3. **Per outbound call**, Dograh derives the templated `wss://…/ws/{ids}` and sends it
   inline in `add_lead`. Nothing persisted.
4. **Per inbound call**, VoiceLink connects to the bare `…/ws`; Dograh reads the `start`
   frame, routes by the called DID (`telephony_phone_numbers`), creates the run, streams.

## Reverse proxy

Your TLS terminator (Caddy/nginx) must pass the **WebSocket upgrade** through to the api
on `/api/v1/telephony/ws`. Caddy's `reverse_proxy api:8000` does this natively. No
TURN/coturn is required — VoiceLink media is server-to-server WSS. See
`env-and-deploy.md`.
