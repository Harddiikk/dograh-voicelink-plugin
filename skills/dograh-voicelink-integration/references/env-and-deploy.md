# Environment & deployment

Everything the api host needs for VoiceLink, plus the reverse-proxy and deploy specifics.

## The one env var that matters: `BACKEND_API_ENDPOINT`

| Env var | Default | Role |
|---|---|---|
| **`BACKEND_API_ENDPOINT`** | `http://localhost:8000` | **The single source of the media WSS URL and the call-events webhook.** `https://`→`wss://`. Must be a public `https://` origin in production, or VoiceLink can't stream audio back. localhost/unset → cloudflared tunnel fallback (dev only). |

Set it in the api environment (e.g. `.env.api`):
```dotenv
BACKEND_API_ENDPOINT=https://api.your-domain.com
ENVIRONMENT=production   # 'local' enables localhost-only WebRTC/TURN shortcuts you don't want
```
Derived at runtime:
- media (both directions): `wss://api.your-domain.com/api/v1/telephony/ws[/{ids}]`
- events webhook: `https://api.your-domain.com/api/v1/telephony/voicelink/events/{run_id}`

## Per-organization credentials are NOT env vars

The per-call VoiceLink credentials — `api_base`, `username`/`password` or `bearer_token`,
`did_number`, `from_numbers`, `client_id` — live in the **DB** (`telephony_configurations`,
JSONB `config`), set via the Settings → Telephony card. They are per-org, not env.

## Optional env vars (only for extras you probably don't need)

These belong to the white-label **reseller-KYC / provisioning** feature, which the
overlay does **not** install. List them only if the user explicitly wants that feature:

| Env var | Default | Role |
|---|---|---|
| `VOICELINK_INBOUND_GAIN` | `1.0` | Boosts decoded inbound PCM (Indian carriers run quiet, starving VAD/ASR). **Live-tunable, no rebuild** — the one extra var worth knowing for call-quality tuning. |
| `VOICELINK_API_BASE` | `https://app.voicelink.co.in/api` | Reseller-KYC API base (provisioning only). |
| `VOICELINK_RESELLER_USERNAME` / `_PASSWORD` | — | Reseller-KYC creds (provisioning only). |
| `VOICELINK_PROVISION_KEY` / `APP_SECRET_KEY` | — | Fernet keys for at-rest secret encryption (provisioning only). |
| `VOICELINK_DEFAULT_CHANNELS` / `_INBOUND_RATE` / `_OUTBOUND_RATE` | `1` | create-client payload defaults (provisioning only). |

## Reverse proxy — WebSocket upgrade is mandatory

The TLS terminator must pass the WebSocket upgrade to the api on `/api/v1/telephony/ws`.

**Caddy** (does WS upgrades natively — nothing special needed):
```caddyfile
api.your-domain.com {
    handle /voice-audio/* { reverse_proxy minio:9000 }   # recordings, if used
    handle { reverse_proxy api:8000 }                     # REST + telephony WS
}
```

**nginx** (must set the upgrade headers explicitly):
```nginx
location /api/v1/telephony/ws {
    proxy_pass http://api:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

- Open ports **80/443** (`ufw allow 80,443/tcp`).
- **No TURN/coturn** is required — VoiceLink media is server-to-server WSS, unaffected by
  NAT/TURN. (In-browser WebRTC *test* calls may still need TURN; real VoiceLink calls
  don't.)

## Docker overlay deploy (the production-proven path)

The overlay image = stock `dograhai/dograh-api:latest` + the provider package + the three
edits. Build it with `assets/docker/build-and-deploy.sh`, push to a registry, and pull on
the host. **Never build on a 2GB VPS** — build elsewhere and `docker compose pull`.

```bash
# build + push from a workstation / CI (these scripts live in the plugin install dir)
"${CLAUDE_PLUGIN_ROOT}/assets/docker/build-and-deploy.sh" --tag ghcr.io/<you>/dograh-api:voicelink --push
# on the VPS: point compose 'api' at that image, then
docker compose pull && docker compose up -d api
```
Make the compose `api` service use the overlay tag (set `image:` directly, or via the
`REGISTRY`/`TAG` vars your compose supports).

No Alembic migration is required — `provider` is a plain string column.

## Memory note (small VPS)

Each uvicorn worker loads the full pipeline stack. On 2–4GB boxes keep
`FASTAPI_WORKERS=1` / `ARQ_WORKERS=1` and add swap before any real call volume. Scale
RAM/CPU/workers before load.

## Verify after deploy

```bash
curl -s https://api.your-domain.com/api/v1/health          # 200
bash "${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh" https://api.your-domain.com   # 101 on /ws + WSS URLs
docker compose logs -f api                                  # watch a live call
```
