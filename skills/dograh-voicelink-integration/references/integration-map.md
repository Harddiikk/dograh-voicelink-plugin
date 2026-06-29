# VOICELINK ↔ DOGRAH INTEGRATION MAP

> How VoiceLink is wired into a Dograh fork as a first-class telephony provider, end to end. This document is reproduction-grade: a future engineer or a Claude Code plugin should be able to recreate the integration on a clean Dograh fork by following it.
>
> Scope note: findings come from 6 readers of the real, working, tested code. Where the code itself flags something as "unconfirmed upstream" (mainly the exact inbound `start`-frame field names and the inbound answer-body keys), this document repeats that caveat rather than presenting it as settled fact.

---

## 1. Overview

**VoiceLink is modeled in Dograh as an ordinary `TelephonyProvider`** (the same abstraction used for Twilio, Plivo, Telnyx, Vonage, Vobiz, Cloudonix, ARI). It plugs into Dograh's provider registry, exposes a config card in the Settings UI like every other provider, and rides the standard pipecat media pipeline. Its canonical provider id everywhere is the string **`voicelink`** (`WorkflowRunMode.VOICELINK = "voicelink"`).

What makes VoiceLink specific:

- **Outbound dialing** is a REST call to VoiceLink's reseller cloud API: `POST {api_base}/v1/add_lead`.
- **Auth** to that API is bearer token *or* username/password (login at `POST /v1/auth/login`, token from `data.access_token`, with a one-shot 401 re-login retry).
- **Media** flows over a single bidirectional WebSocket using a **Twilio-Media-Streams-style JSON envelope** carrying **G.711 A-law audio at 8 kHz** (`audio/alaw`, *not* mu-law), base64-encoded.
- **Call-lifecycle events** arrive as **unsigned** webhook POSTs with a nested camelCase body.

### The headline fact: ONE WSS URL serves BOTH inbound and outbound

VoiceLink uses **one media WebSocket protocol, one codec (A-law 8 kHz), one serializer, and one WSS base** for both call directions. They converge on the exact same pipeline entrypoint (`run_pipeline_telephony(provider_name="voicelink", ...)`) and the exact same transport factory (`create_transport`). They differ in **only two ways**:

1. **The URL path suffix.**
   - **Outbound:** `wss://<host>/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}` — the run is pre-created at dial time, so its identity is baked into the path. This path is served by Dograh's **generic** telephony WS route in `api/routes/telephony.py`.
   - **Inbound:** `wss://<host>/api/v1/telephony/ws` — the **bare** path, no run id, because no run exists yet. This is served by a **VoiceLink-specific** route `@router.websocket("/ws")` in `providers/voicelink/routes.py`.

2. **Who creates the `WorkflowRun` and how it is identified.**
   - **Outbound:** run already exists; identity is in the URL path.
   - **Inbound:** the handler reads the `start` frame, extracts the **called DID**, looks it up against `telephony_phone_numbers` (the DID *is* the authorization boundary — VoiceLink's inbound frame carries no account/reseller id), creates the run on the fly via `_create_inbound_workflow_run`, then runs the same pipeline.

The `<host>` part of that WSS URL is **not stored anywhere** and **not a config field**. It is derived at runtime from the env var **`BACKEND_API_ENDPOINT`** by swapping the scheme (`http`→`ws`, `https`→`wss`) and appending the fixed path `/api/v1/telephony/ws`. So "the single WSS URL" = `BACKEND_API_ENDPOINT` (with scheme swapped) + `/api/v1/telephony/ws`; inbound uses it bare, outbound appends `/{workflow_id}/{user_id}/{workflow_run_id}`.

> **Premise correction (important):** there is **no** UI field where you paste a VoiceLink WSS URL, and **no** WSS column in the telephony config DB row. The only URL-shaped placeholder on the card is the **API Base URL** field, whose placeholder is `https://app.voicelink.co.in/api` — that is VoiceLink's **REST API base** (the direction *we → VoiceLink* for `add_lead`/login), **not** the media WSS URL (the direction *VoiceLink → us* for audio). Do not conflate them.

---

## 2. File manifest

### Provider package (all NEW) — `api/services/telephony/providers/voicelink/`

| File | NEW/MOD | Role |
|---|---|---|
| `__init__.py` | **NEW** | Builds `ProviderSpec` + `ProviderUIMetadata` and calls `register(SPEC)` at import time. Defines `_config_loader` (reshapes the stored DB dict) and `_UI_METADATA` (the card's fields/placeholders). |
| `provider.py` | **NEW** | `VoiceLinkProvider(TelephonyProvider)`. Number normalization, auth + HTTP with 401-relogin-retry, outbound `initiate_call` (`POST /v1/add_lead`, **builds the outbound WSS URL**), webhook event→status parsing, the OUTBOUND `handle_websocket`, inbound helpers (`start_inbound_stream`, `parse_inbound_webhook`). |
| `transport.py` | **NEW** | `create_transport(...)` factory — builds the pipecat `FastAPIWebsocketTransport` wired to `VoiceLinkFrameSerializer`. Used by **both** directions. |
| `serializers.py` | **NEW** | `VoiceLinkFrameSerializer` — the wire codec (A-law 8 kHz, Twilio-streams-style JSON). |
| `config.py` | **NEW** | `VoiceLinkConfigurationRequest` / `VoiceLinkConfigurationResponse` Pydantic schemas + `DEFAULT_VOICELINK_API_BASE`. No WSS field. |
| `routes.py` | **NEW** | Module-level `router = APIRouter()`. The `POST /voicelink/events/{workflow_run_id}` lifecycle webhook **and** the bare inbound `@router.websocket("/ws")`. |

### Backend wiring (MODIFIED shared files)

| File | NEW/MOD | Change |
|---|---|---|
| `api/enums.py` | **MOD** | `VOICELINK = "voicelink"` added to `WorkflowRunMode` (line 26). The load-bearing provider string. |
| `api/services/telephony/providers/__init__.py` | **MOD** | `voicelink` added to the side-effect import tuple (line 16) so `register(SPEC)` runs at startup. **This is the only non-folder edit strictly required for registration.** |
| `api/schemas/telephony_config.py` | **MOD** | Imports `VoiceLinkConfigurationRequest/Response`; adds the request to the `TelephonyConfigRequest = Annotated[Union[...], Field(discriminator="provider")]`; adds `voicelink: Optional[VoiceLinkConfigurationResponse] = None` to `TelephonyConfigurationResponse`; both added to `__all__`. |
| `api/constants.py` | **MOD (pre-existing var)** | `BACKEND_API_ENDPOINT = os.getenv("BACKEND_API_ENDPOINT", "http://localhost:8000")` (line 22). The single source of the WSS host. (A signup-provisioning comment was also added near lines 40–44.) |

### Provisioning / reseller-KYC feature (NEW — white-label SaaS layer, *separate* from the media path)

> These are part of a white-label fork's "create/manage VoiceLink reseller client" feature. They are **not** required for inbound/outbound media to work, but they are part of "how VoiceLink is integrated" in the source fork. The referenced design spec (`2026-06-16-admin-clients...`) covers *this* feature and explicitly marks inbound + media-WSS work **out of scope** — so the single-WSS / inbound+outbound design intent lives in code docstrings, not that spec.

| File | NEW/MOD | Role |
|---|---|---|
| `api/services/voicelink_kyc/client.py` | **NEW** | Reseller KYC / client-provisioning HTTP client. Reads `VOICELINK_API_BASE`, `VOICELINK_RESELLER_USERNAME`, `VOICELINK_RESELLER_PASSWORD`. `is_configured` false → KYC routes return 503. |
| `api/services/voicelink_clients/secrets.py` | **NEW** | Reversible Fernet encryption of the org provisioning password. Owns `VOICELINK_PROVISION_KEY`. |
| `api/services/voicelink_clients/service.py` | **NEW** | Builds the create-client payload using `VOICELINK_DEFAULT_CHANNELS` / `_INBOUND_RATE` / `_OUTBOUND_RATE`. |
| `api/utils/secret_crypto.py` | **NEW/MOD** | At-rest org-secret encryption; `_KEY_ENVS = ("APP_SECRET_KEY", "VOICELINK_PROVISION_KEY")` — `APP_SECRET_KEY` primary, `VOICELINK_PROVISION_KEY` fallback. |
| `api/alembic/versions/c9e2f5a17d04_add_voicelink_provisioning_to_organizations.py` | **NEW** | Migration: VoiceLink provisioning columns on organizations. |
| `api/alembic/versions/a7f3c1e9b2d6_add_voicelink_provision_secret.py` | **NEW** | Migration: encrypted provision-secret column. |

### Tests (NEW) — `api/tests/telephony/voicelink/`

| File | Role |
|---|---|
| `test_provider.py` | Pins normalization, `add_lead` payload shape, computed `websocket_url`/`webhook_url`, `CallInitiationResult`, 401 retry semantics, event→status map, `validate_config` truth table. |
| `test_serializer.py` | Pins A-law media-event shape, `len(alaw) == len(pcm)//2`, round-trip error `< 1024`, clear-on-interruption, DTMF, non-media→`None`. |
| `test_routes.py` | Pins the events route's sentinel-dict returns and "never raises". |

### Env / deploy (MODIFIED)

| File | NEW/MOD | Role |
|---|---|---|
| `api/.env.example` | **MOD** | Documents (commented) `VOICELINK_API_BASE`, `VOICELINK_RESELLER_USERNAME/PASSWORD` (lines 49–54); `BACKEND_API_ENDPOINT=http://localhost:8000` (line 8). |
| `deploy/vps/.env.api.example` | **MOD** | Production template: `BACKEND_API_ENDPOINT=https://api.your-domain.com`, `APP_SECRET_KEY`, `VOICELINK_API_BASE`, `VOICELINK_RESELLER_USERNAME=CHANGEME`, `VOICELINK_RESELLER_PASSWORD=CHANGEME`. |
| `deploy/vps/DEPLOY.md` | **MOD** | Section 5 = VoiceLink post-deploy config; restates that both the wss media URL and the events webhook derive from `BACKEND_API_ENDPOINT`; "no TURN server" quirk. |

### Pre-existing, UNCHANGED, but load-bearing (registry/convention-driven — do **not** edit per provider)

| File | Why it matters |
|---|---|
| `api/routes/telephony.py` | Hosts the **generic** outbound WS route `@router.websocket("/ws/{workflow_id}/{user_id}/{workflow_run_id}")`; `_mount_provider_routers()` auto-mounts each provider's `routes.py`; `_create_inbound_workflow_run`. |
| `api/utils/common.py` | `get_backend_endpoints()` — converts `BACKEND_API_ENDPOINT` into the `(http, wss)` pair. |
| `api/services/pipecat/run_pipeline.py` | `run_pipeline_telephony` — provider-agnostic bootstrap; calls `spec.transport_factory`. |
| `api/services/pipecat/audio_config.py` | `create_audio_config` reads `spec.transport_sample_rate` (8000 for voicelink). |
| `api/services/telephony/registry.py` | `ProviderSpec` dataclass + `register`/`get`/`all_specs`. **Routes are deliberately NOT on the spec.** |
| `api/routes/main.py`, `api/app.py` | Establish the `/api/v1` + `/telephony` prefix chain. |
| `ui/src/components/telephony/ConfigFormDialog.tsx` | The telephony card. **Fully metadata-driven** — needs zero per-provider code. |

---

## 3. The single WSS URL

### What it is

- **Config field / env var:** there is **no** WSS config field and **no** dedicated WSS env var. The host comes from the global env var **`BACKEND_API_ENDPOINT`**.
- **Where the host default lives (backend):** `api/constants.py` line 22:
  ```python
  BACKEND_API_ENDPOINT = os.getenv("BACKEND_API_ENDPOINT", "http://localhost:8000")
  ```
- **Placeholder in the UI telephony card:** the **only** URL placeholder rendered on the VoiceLink card is the `api_base` field's placeholder string **`https://app.voicelink.co.in/api`** (defined in backend metadata `providers/voicelink/__init__.py`, *not* in the UI). **That is the REST API base, not the WSS URL — it is a red herring for anyone hunting for "where you paste the WSS URL."**
- **Placeholder string for the WSS host:** effectively the `BACKEND_API_ENDPOINT` default `http://localhost:8000` (which yields plain `ws://localhost:8000`, **not** `wss://`). To get a real `wss://`, `BACKEND_API_ENDPOINT` **must** be a public `https://` URL.
- **Fixed path:** `/api/v1/telephony/ws` (assembled from `app.py` `API_PREFIX="/api/v1"` + telephony router `prefix="/telephony"` + `/ws`).

### How the scheme is produced — `api/utils/common.py` `get_backend_endpoints()`

```python
scheme = get_scheme(BACKEND_API_ENDPOINT)
if scheme:
    http_url = BACKEND_API_ENDPOINT.rstrip("/")
    ws_scheme = {"http": "ws", "https": "wss"}[scheme]
    ws_url = BACKEND_API_ENDPOINT.rstrip("/").replace(scheme, ws_scheme, 1)
```

Returns `(backend_endpoint, wss_backend_endpoint)`. If `BACKEND_API_ENDPOINT` is `localhost`/`127.0.0.1` (or unset), it falls back to a **cloudflared tunnel URL** via `TunnelURLProvider.get_tunnel_urls()`. If neither env nor tunnel is available, it raises a descriptive `ValueError`.

### Data flow: "user pastes URL" → stored → used

The literal flow the prompt asks about (a user pasting a URL) does **not** happen for the WSS URL. The accurate flow is:

1. **Operator/deployer sets `BACKEND_API_ENDPOINT`** in the API host's environment (e.g. `deploy/vps/.env.api` → `BACKEND_API_ENDPOINT=https://api.your-domain.com`). This is the *only* configurable piece. It is **not** stored in the DB.
2. **At call time**, `get_backend_endpoints()` reads it and returns the `(https, wss)` pair.
3. **Outbound:** `provider.initiate_call()` concatenates the fixed path + run ids and ships it **inline** to VoiceLink in the `add_lead` body (`websocket_url`). Nothing is persisted.
4. **Inbound:** the operator pastes the bare `wss://<host>/api/v1/telephony/ws` into **VoiceLink's own portal/bot config** (external to this repo) so incoming calls connect there.

What *is* pasted into the **Dograh UI card** (and stored in the DB `telephony_configurations.config` JSONB) is the VoiceLink **REST credential set**: `api_base`, `username`/`password` or `bearer_token`, `did_number`, `from_numbers`, optional `client_id`. None of these is the WSS URL.

---

## 4. Backend wiring steps (with load-bearing snippets)

### 4.1 Enum / constant

`api/enums.py` (line 26):
```python
class WorkflowRunMode(Enum):
    ...
    VOICELINK = "voicelink"
```
This string is the registry key, `VoiceLinkProvider.PROVIDER_NAME`, the DB `provider` column value, and the importlib router-discovery key. It is load-bearing everywhere.

### 4.2 Provider registry entry — `providers/voicelink/__init__.py`

```python
SPEC = ProviderSpec(
    name="voicelink",
    provider_cls=VoiceLinkProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=VoiceLinkConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VoiceLinkConfigurationResponse,
    account_id_credential_field="username",
)
register(SPEC)
```

`_config_loader` reshapes the stored DB dict → `{provider, api_base, username, password, bearer_token, did_number, from_numbers}`. **Known wiring gap:** `_config_loader` (lines 17–26) does **not** pass through `client_id`, even though `config.py` and `_UI_METADATA` define it. `account_id_credential_field="username"` exists because VoiceLink inbound has no account id (DID is used instead).

`ProviderSpec` carries **no** router field — routes are discovered separately by path convention (see 4.3).

### 4.3 Route mounting (automatic, convention-driven)

Add `voicelink` to the side-effect import tuple in `api/services/telephony/providers/__init__.py` (line 16):
```python
from api.services.telephony.providers import (  # import for side effects (registration)
    ari, cloudonix, plivo, telnyx, twilio, vobiz, voicelink, vonage,
)
```

Routes mount themselves via `api/routes/telephony.py`:
```python
def _mount_provider_routers() -> None:
    import importlib
    from api.services.telephony import registry as _telephony_registry
    for spec in _telephony_registry.all_specs():
        try:
            module = importlib.import_module(f"api.services.telephony.providers.{spec.name}.routes")
        except ModuleNotFoundError:
            continue
        provider_router = getattr(module, "router", None)
        if provider_router is not None:
            router.include_router(provider_router)

_mount_provider_routers()
```
So `providers/voicelink/routes.py` (its module-level `router`) is mounted with no manual `include_router`. Prefix chain (`api/app.py`):
```python
API_PREFIX = "/api/v1"
api_router.include_router(main_router)
app.include_router(api_router, prefix=API_PREFIX)
```
Net full paths: `/api/v1/telephony/voicelink/events/{run_id}` and `/api/v1/telephony/ws` (inbound).

> **Why routes are off the spec:** `ProviderSpec`'s docstring states routes are intentionally not on the spec, so importing a provider *class* doesn't pull the heavy route module. Also, `voicelink/routes.py` is imported *by* `api.routes.telephony`, so the inbound handler uses **lazy in-function imports** (sqlalchemy, models, `_create_inbound_workflow_run`, `run_pipeline_telephony`) to avoid a circular import.

### 4.4 Config schema — `providers/voicelink/config.py`

```python
DEFAULT_VOICELINK_API_BASE = "https://app.voicelink.co.in/api"  # line 7

class VoiceLinkConfigurationRequest(BaseModel):
    provider: Literal["voicelink"] = Field(default="voicelink")   # discriminator
    api_base: str = Field(default=DEFAULT_VOICELINK_API_BASE)
    username: Optional[str] = None
    password: Optional[str] = None
    bearer_token: Optional[str] = None
    did_number: str = Field(...)                                  # REQUIRED
    from_numbers: List[str] = []
    client_id: Optional[str] = None

    @model_validator(...)
    def _require_credentials(self):  # bearer_token OR (username AND password)
        ...
```
`VoiceLinkConfigurationResponse` mirrors these and masks sensitive fields. **There is no `websocket`/WSS field in either schema** — the WSS URL is computed at call time, never stored.

### 4.5 Serializer wiring into the shared union — `api/schemas/telephony_config.py`

```python
from api.services.telephony.providers.voicelink.config import (
    VoiceLinkConfigurationRequest, VoiceLinkConfigurationResponse,
)

TelephonyConfigRequest = Annotated[
    Union[..., VoiceLinkConfigurationRequest, ...],
    Field(discriminator="provider"),
]

class TelephonyConfigurationResponse(BaseModel):
    ...
    voicelink: Optional[VoiceLinkConfigurationResponse] = None
```
(Both names also exported in `__all__`.)

### 4.6 Transport — `providers/voicelink/transport.py`

```python
async def create_transport(
    websocket, workflow_run_id, audio_config, organization_id, *,
    ambient_noise_config, telephony_configuration_id, is_realtime,
    stream_id, call_id,
):
    await load_credentials_for_transport(..., expected_provider="voicelink")
    serializer = VoiceLinkFrameSerializer(
        stream_sid=stream_id, call_sid=call_id,
        params=InputParams(
            voicelink_sample_rate=8000,
            sample_rate=audio_config.pipeline_sample_rate,
        ),
    )
    mixer = build_audio_out_mixer(...)
    return FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True, audio_out_enabled=True,
            audio_in_sample_rate=audio_config..., audio_out_sample_rate=audio_config...,
            audio_out_mixer=mixer, serializer=serializer,
            **realtime_param_overrides(is_realtime),
        ),
    )
```
The same factory serves both directions (both reach `run_pipeline_telephony(provider_name="voicelink")`).

### 4.7 The provider-agnostic pipeline — `api/services/pipecat/run_pipeline.py`

```python
spec = telephony_registry.get(provider_name)
audio_config = create_audio_config(provider_name, is_realtime=is_realtime)
transport = await spec.transport_factory(
    websocket, workflow_run_id, audio_config, workflow.organization_id,
    ambient_noise_config=ambient_noise_config,
    telephony_configuration_id=telephony_configuration_id,
    is_realtime=is_realtime, **transport_kwargs,
)
```
`transport_kwargs` carries `{stream_id, call_id}` from the provider's `start`-event parse. **No name branching** — fully registry-driven. `is_realtime` forces a 16 kHz pipeline over the 8 kHz A-law wire; the serializer's resampler bridges.

### 4.8 Outbound URL construction + `add_lead` — `providers/voicelink/provider.py`

```python
backend_endpoint, wss_backend_endpoint = await get_backend_endpoints()

websocket_url = (
    f"{wss_backend_endpoint}/api/v1/telephony/ws"
    f"/{workflow_id}/{user_id}/{workflow_run_id}"
)
events_url = (
    f"{backend_endpoint}/api/v1/telephony/voicelink/events/{workflow_run_id}"
)

payload = {
    "did_number": did_number,            # 91-prefixed registered caller id
    "customer_number": customer_number,  # bare 10-digit local number
    "custom_parameters": json.dumps({...workflow_id/user_id/workflow_run_id...}),
    "websocket_url": websocket_url,      # inline, no answer-URL step
    "webhook_url": events_url,
}
status, data = await self._api_request("POST", "/v1/add_lead", payload)
```
Tests pin this byte-for-byte: with `get_backend_endpoints` patched to `("https://example.test", "wss://example.test")`, `payload["websocket_url"] == "wss://example.test/api/v1/telephony/ws/7/11/123"` and the events URL `== "https://example.test/api/v1/telephony/voicelink/events/123"`.

**Class constants / behavior contracts:**
- `PROVIDER_NAME = WorkflowRunMode.VOICELINK.value` (`"voicelink"`); `WEBHOOK_ENDPOINT = "voicelink/events"`; `DEFAULT_API_BASE = "https://app.voicelink.co.in/api"`.
- **Number normalization** (asymmetric, intentional):
  ```python
  digits = re.sub(r"\D", "", raw or "")
  if len(digits) == 12 and digits.startswith("91"):
      return digits[2:]
  if len(digits) == 11 and digits.startswith("0"):
      return digits[1:]
  return digits
  ```
  `customer_number` **must** be bare 10-digit (a 91-prefixed customer number fails the carrier with Q.850 cause 38). `did_number` keeps its 91-prefixed registered form. (Note the exact-12 guard means a 10-digit number that happens to start `91` is *not* stripped.)
- **Auth / retry:**
  ```python
  token = self._access_token or await self._login()
  url = f"{self.api_base}{path}"
  status, data = await self._send_request(method, url, payload, token)
  if status == 401 and self.username and self.password:
      token = await self._login()
      status, data = await self._send_request(method, url, payload, token)
  return status, data
  ```
  One 401 re-login retry only when username+password exist; a static bearer-only config returns the 401. `_login()` reads `data.access_token` and **never logs the password**.
- `initiate_call` returns `CallInitiationResult(call_id=str(outbound_queue_id) or "voicelink-run-{run}", status="queued", caller_number=did_number, provider_metadata={outbound_queue_id, bot_id, client_id, carrier_id})`. On non-2xx → `HTTPException(status if >=400 else 502)`.
- `parse_status_callback` maps nested camelCase `{event, call:{...}}` via `_EVENT_STATUS` (`call.initiated/ringing`, `answered`→`in-progress`, `completed/ended`→`completed`, `failed`); unknown events pass through; tolerates a missing `call` key; recording URL read from `recordingUrl` **or** `recording_url` (spelling guessed defensively).
- `validate_config()` truth table: `api_base AND did_number AND (bearer_token OR (username AND password))`.
- `handle_websocket()` reads `connected` then `start`, extracts `stream_sid`/`streamSid` + `call_sid`, closes `4400` if no `stream_sid`, then `run_pipeline_telephony(..., transport_kwargs={stream_id, call_id})`.
- `transfer_call` raises `NotImplementedError`; `supports_transfers()` returns `False`. `get_call_status` → `"unknown"`; `get_call_cost` → zeros. `get_webhook_response` returns `""` (no answer-markup step — both URLs are passed inline).

### 4.9 Inbound entrypoint — `providers/voicelink/routes.py`

```python
@router.post("/voicelink/events/{workflow_run_id}")
async def handle_voicelink_events(request: Request, workflow_run_id: int):
    ...

@router.websocket("/ws")
async def voicelink_inbound_ws(websocket: WebSocket) -> None:
    """VoiceLink WS-only INBOUND entrypoint.

    VoiceLink uses ONE media WebSocket for both directions. Outbound calls
    connect to ``/ws/{workflow_id}/{user_id}/{workflow_run_id}`` (the run is
    pre-created by add_lead). INBOUND calls connect here to the bare bot URL
    (``/api/v1/telephony/ws``) with NO run id, so we read the ``start`` event,
    route by the called DID, create an inbound run, and run the pipeline."""
```

Inbound flow: `accept()` → read `connected`/`start` → log the **full raw start frame** → `pick()` the called DID (`to`/`to_number`/`called`/`did`…) and caller (`from`/`caller`) from `start_data`/`custom_parameters`/`start_msg` → `normalize_telephony_address(..., country_hint="IN")` → **DID routing** via inline join:

```python
select(TelephonyConfigurationModel, TelephonyPhoneNumberModel)
.join(
    TelephonyPhoneNumberModel,
    TelephonyPhoneNumberModel.telephony_configuration_id == TelephonyConfigurationModel.id,
)
.where(
    TelephonyConfigurationModel.provider == "voicelink",
    TelephonyPhoneNumberModel.address_normalized == to_norm,
    TelephonyPhoneNumberModel.is_active.is_(True),
)
```

→ read `phone_row.inbound_workflow_id` → `_create_inbound_workflow_run(...)` → set state `RUNNING` → `run_pipeline_telephony(provider_name="voicelink", transport_kwargs={stream_id, call_id})`.

The events route never raises:
```python
except (UnicodeDecodeError, json.JSONDecodeError):
    return {"status": "error", "reason": "invalid_json"}
...
if not workflow_run:
    return {"status": "ignored", "reason": "workflow_run_not_found"}
...
# happy path
return {"status": "success"}
```

`provider.start_inbound_stream()` returns a JSON answer body `{status, action: "stream", websocket_url, webhook_url, media_format: {encoding: "audio/alaw", sample_rate: 8000}}` for the generic HTTP-webhook inbound path, mirroring `add_lead`'s inline fields.

> **Unconfirmed-upstream caveats (flagged in code):** the exact location of the called/caller DID in VoiceLink's inbound `start` frame, and the exact key names of the inbound answer body, are **guesses** that mirror `add_lead`. The full raw start frame is logged precisely so a real inbound call reveals the truth. Inbound is best treated as **implemented but unverified**.

### 4.10 Serializer wire protocol — `providers/voicelink/serializers.py`

```python
# serialize (pipeline -> VoiceLink)
if isinstance(frame, InterruptionFrame):
    return json.dumps({"event": "clear", "stream_sid": self._stream_sid})   # barge-in
elif isinstance(frame, AudioRawFrame):
    serialized_data = await pcm_to_alaw(data, frame.sample_rate,
                                        self._voicelink_sample_rate, self._output_resampler)
    payload = base64.b64encode(serialized_data).decode(...)
    return json.dumps({"event": "media", "stream_sid": self._stream_sid,
                       "media": {"payload": payload}})
# OutputTransportMessage(Urgent)Frame -> raw frame.message JSON

# deserialize (VoiceLink -> pipeline)
# 'media' -> b64decode + alaw_to_pcm(payload, self._voicelink_sample_rate, self._sample_rate, ...)
#            -> InputAudioRawFrame(num_channels=1), optional VOICELINK_INBOUND_GAIN boost, RMS logged every 50 frames
# 'dtmf'  -> InputDTMFFrame(KeypadEntry(digit))
# 'transfer' -> logged, returns None
# connected/start/mark/stop -> None
```
- Codec: **G.711 A-law (`audio/alaw`) at 8 kHz** — `InputParams(voicelink_sample_rate=8000, sample_rate=<pipeline rate>)`. **Not mu-law.**
- Outbound messages use **snake_case** `stream_sid`; inbound `start` is read with snake_case primary + camelCase fallback.
- `custom_parameters` is a JSON **string** in `add_lead`, but a nested **object** in webhooks.
- Tested invariants: `len(alaw) == len(pcm)//2`; round-trip quantization error `< 1024`; empty payload → `None`.

---

## 5. UI telephony-card

**File:** `ui/src/components/telephony/ConfigFormDialog.tsx` (a modal "card"). **Zero per-provider code** — it is fully metadata-driven.

**What it renders for VoiceLink** (from backend `_UI_METADATA`, fetched via `getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet`):
- A Name field (placeholder `e.g. Twilio US prod`).
- A provider `<Select>` (by `display_name`; locked when editing) — VoiceLink (`display_name="VoiceLink"`) appears automatically.
- One `FieldInput` per `currentProvider.fields`: **API Base URL** (placeholder `https://app.voicelink.co.in/api`), **Username** (sensitive→`type=password`), **Password**, **Bearer Token**, **DID Number**, **Phone Numbers** (string-array; skipped by the form UI), **Client ID**.
- An optional "Set as default for outbound calls" toggle.

**There is NO WSS-URL field.** Placeholders are read generically from backend metadata:
```tsx
const placeholder =
    field.placeholder ??
    (field.sensitive && isEdit ? "Leave masked to keep existing" : "");
```

`_UI_METADATA` (backend, `providers/voicelink/__init__.py`):
```python
ProviderUIField(
    name="api_base",
    label="API Base URL",
    type="text",
    required=False,
    description="VoiceLink API base URL",
    placeholder="https://app.voicelink.co.in/api",
),
# display_name="VoiceLink", docs_url="https://docs.dograh.com/integrations/telephony/voicelink"
```

**Save flow:** `handleSubmit` builds `configPayload = { provider: providerName, ...values }` and calls `POST createTelephonyConfigurationApiV1OrganizationsTelephonyConfigsPost` (or `PUT ...ConfigIdPut` when editing) with body `{ name, is_default_outbound, config: { provider: "voicelink", ...values } }` and Bearer auth from `getAccessToken()`. Errors surface via toast through `detailFromError`; `TelephonyConfigWarningsContext` flags org-level misconfiguration.

**DIDs / inbound binding** are added afterward on the config detail page `/telephony-configurations/[configId]` via `PhoneNumberDialog`, which is where a DID is bound to an `inbound_workflow_id` (creating the `telephony_phone_numbers` row the inbound handler routes against).

---

## 6. Env & deploy

### Full VoiceLink-related env var inventory

| Env var | Type / default | Role |
|---|---|---|
| **`BACKEND_API_ENDPOINT`** | url, default `http://localhost:8000` | **The single source of the media WSS URL and the events webhook URL.** `https://`→`wss://`. Must be public `https://` in prod (e.g. `https://api.your-domain.com`). `localhost`/unset → cloudflared tunnel fallback. |
| `VOICELINK_API_BASE` | url, default `https://app.voicelink.co.in/api` | VoiceLink's **own** cloud REST API base (direction: us → VoiceLink) for reseller KYC/provisioning. Distinct from the per-org `api_base` DB field. |
| `VOICELINK_RESELLER_USERNAME` | str (`CHANGEME`) | Reseller KYC creds. Unset → KYC page "not configured", KYC routes 503. |
| `VOICELINK_RESELLER_PASSWORD` | str (`CHANGEME`) | Reseller KYC creds. |
| `VOICELINK_PROVISION_KEY` | Fernet key (urlsafe base64, 32 bytes) | Reversibly encrypts the org provisioning password. Also a **fallback** at-rest key. Unset → no-op + warning; invalid → treated as unset. |
| `APP_SECRET_KEY` | Fernet key | **Primary** at-rest org-secret key; `VOICELINK_PROVISION_KEY` is its fallback. Neither set → org secrets stored **UNENCRYPTED** (warning logged). |
| `VOICELINK_DEFAULT_CHANNELS` | int, default `1` | create-client payload default. |
| `VOICELINK_DEFAULT_INBOUND_RATE` | float, default `1` | create-client payload default. |
| `VOICELINK_DEFAULT_OUTBOUND_RATE` | float, default `1` | create-client payload default. |
| `VOICELINK_INBOUND_GAIN` | float, default `1.0` | Boosts decoded inbound PCM (Indian carriers run quiet, starving VAD/ASR). Read in `serializers.py`. Live-tunable without a rebuild. |

> The **per-call** VoiceLink credentials (`username`/`password`/`bearer_token`/`did_number`/`api_base`/`client_id`) are **per-organization DB rows** (`TelephonyConfigurationModel`, JSONB `config`), configured in the Settings → Telephony UI — **not** env vars.

### Example `deploy/vps/.env.api`

```dotenv
# Public origin — derives BOTH the wss media URL and the events webhook
BACKEND_API_ENDPOINT=https://api.your-domain.com
ENVIRONMENT=production

# At-rest secret encryption (Fernet)
APP_SECRET_KEY=<urlsafe-base64-32-byte-key>
# VOICELINK_PROVISION_KEY=<urlsafe-base64-32-byte-key>   # fallback / provisioning

# VoiceLink reseller KYC + provisioning (our server -> VoiceLink cloud API)
VOICELINK_API_BASE=https://app.voicelink.co.in/api
VOICELINK_RESELLER_USERNAME=CHANGEME
VOICELINK_RESELLER_PASSWORD=CHANGEME

# Optional provisioning / tuning defaults
# VOICELINK_DEFAULT_CHANNELS=1
# VOICELINK_DEFAULT_INBOUND_RATE=1
# VOICELINK_DEFAULT_OUTBOUND_RATE=1
# VOICELINK_INBOUND_GAIN=1.0
```

### Deploy notes (`deploy/vps/DEPLOY.md` §5)

- `BACKEND_API_ENDPOINT` must be `https://` and **publicly reachable**, or the scheme swap yields an unreachable `wss://` and VoiceLink cannot stream audio. Derived URLs: `wss://api.your-domain.com/api/v1/telephony/ws/...` (media) and `https://api.your-domain.com/api/v1/telephony/voicelink/events/...` (events).
- Ports 80/443 open; **Caddy** terminates TLS and proxies `api:8000` **with WebSocket upgrade** for the `/api/v1/telephony/ws` path.
- **No TURN/coturn required** — VoiceLink media is server-to-server WSS, unaffected by TURN.
- Health check: `curl https://api.your-domain.com/api/v1/health`; `docker compose logs -f api` / `caddy` (Caddy logs surface TLS + WS-upgrade issues).
- Restart the api after editing `.env.api` (`docker compose up -d`).
- On VoiceLink's reseller/bot panel, point the inbound bot's media WebSocket at the **bare** `wss://<your-domain>/api/v1/telephony/ws`. Outbound needs no panel config — `add_lead` carries `websocket_url`/`webhook_url` inline.

---

## 7. Tests / correctness contract

Tests live at `api/tests/telephony/voicelink/{test_provider,test_serializer,test_routes}.py` and mock these exact seams (keep import paths intact when reproducing): `_api_request` / `_send_request` / `_login`, `get_backend_endpoints`, `db_client`, `get_telephony_provider_for_run`, `_process_status_update`.

**Must hold true:**

*Provider (`test_provider.py`):*
- `normalize_customer_number`: `91XXXXXXXXXX` (12 digits) → strip `91`; `0XXXXXXXXXX` (11 digits) → strip `0`; otherwise unchanged (a 10-digit `9184012929` is kept).
- `add_lead` payload shape: bare-10-digit `customer_number`, 91-kept `did_number`, **JSON-string** `custom_parameters`, computed `websocket_url` == `wss://example.test/api/v1/telephony/ws/7/11/123`, `webhook_url` == `.../voicelink/events/123`.
- `CallInitiationResult`: `call_id == str(outbound_queue_id)`, `status == "queued"`, `caller_number == did_number`.
- Explicit `from_number` wins over config DID; missing routing ids → `ValueError`; provider error → `HTTPException(422)`.
- Auth: login-first when no token; one 401 re-login+retry carrying the fresh token; **no** retry when only a static bearer token exists (returns the 401).
- Event→status map: `answered`→`in-progress`, `completed`/`ended`→`completed`; unknown event passes through; defensive `recordingUrl`/`recording_url`; tolerates missing `call`.
- `validate_config` truth table (§4.8).

*Serializer (`test_serializer.py`):*
- A-law media-event shape `{"event": "media", "stream_sid", "media": {"payload"}}`; `len(alaw) == len(pcm)//2`; round-trip error `< 1024`; `InterruptionFrame` → `{"event": "clear"}`; DTMF deserializes; non-media events → `None`; empty payload → `None`.

*Routes (`test_routes.py`):*
- `handle_voicelink_events` returns `{"status": "success"}` on the happy path, `{"status": "ignored", "reason": "workflow_run_not_found"|"workflow_not_found"}` on missing entities, `{"status": "error", "reason": "invalid_json"}` on bad bodies — and **never raises / never 500s** (so VoiceLink retries don't storm). `_process_status_update` is called only on the happy path with a correctly-mapped `StatusCallbackRequest`.

**Invariants the design relies on:** provider string `"voicelink"` is load-bearing across enum / `config_loader` / DB columns / importlib router discovery; codec is A-law 8 kHz (not mu-law); the WSS URL is computed, never stored; DID uniqueness is the inbound authorization boundary.

---

## 8. Debugging

### What's present
- **Loguru, run-scoped** (`[run {workflow_run_id}]` via `set_current_run_id()`).
- **Outbound:** the full `add_lead` payload (did / customer / `websocket_url` / `webhook_url`) is logged at INFO — the exact WSS URL handed to VoiceLink is visible in logs. `add_lead` failures log HTTP status + body.
- **Inbound:** the **full raw start frame** is logged (`VoiceLink INBOUND start frame (raw): ...`) precisely because the DID location is unconfirmed upstream, then the parsed `to`/`from`/`stream_sid`/`call_sid`.
- **Serializer:** measures inbound RMS, logs ~once/sec (every 50 frames); `VOICELINK_INBOUND_GAIN` tunable live.
- **Explicit WS close codes:** `4400` (missing/expected `start`, no `stream_sid`, no DID), `4404` (DID not configured / no `inbound_workflow_id` / workflow missing), `4409` (run not initialized), `1011` (internal). `WebSocketDisconnect` before `start` is treated as an expected end-of-call (info, not error).
- **Events route:** returns structured sentinel dicts; tolerates non-JSON bodies.
- **Secrets:** `_login` never logs the password; `secrets.py` / `utils/secret_crypto.py` warn when the Fernet key is unset/invalid.
- **`get_backend_endpoints()`:** logs chosen HTTP/WS URLs at DEBUG; raises a clear `ValueError` when neither env nor tunnel is available; `_validate_url` enforces scheme/host/port.

### Known gaps / what a proper debugging layer should add
- **Webhooks are unsigned** — `verify_webhook_signature` / `verify_inbound_signature` / `validate_account_id` all return `True`. The **DID match is the only inbound authorization boundary.** A hardening layer would add a shared-secret/HMAC on `/voicelink/events`.
- **No end-to-end WSS upgrade healthcheck** — confirmation currently relies on a live call or the inbound start-frame log. Add a synthetic WS-upgrade probe against `/api/v1/telephony/ws`.
- **Unconfirmed-upstream fields** — inbound `start`-frame DID location and the inbound answer-body keys are best-effort guesses (`pick()` over multiple spellings; full frame logged). Capture a real inbound call, then pin the keys and add a regression test.
- **Stubs** — `get_call_status` → `"unknown"`, `get_call_cost` → zeros, `transfer_call` → `NotImplementedError`, `recordingUrl` spelling guessed. If VoiceLink exposes per-call status/cost APIs, wire them.
- **`_config_loader` omits `client_id`** even though config + UI define it — fix the loader or the `add_lead` metadata will silently lose `client_id`.

---

## 9. Reproduction checklist (fresh Dograh fork)

1. **Enum.** Add `VOICELINK = "voicelink"` to `WorkflowRunMode` in `api/enums.py`. No DB enum migration is needed — `provider` is stored as a plain string.
2. **Create the package** `api/services/telephony/providers/voicelink/` with six files: `config.py`, `provider.py`, `serializers.py`, `transport.py`, `routes.py`, `__init__.py` (signatures/snippets in §4).
3. **`config.py`** — `VoiceLinkConfigurationRequest`/`Response`: `provider: Literal["voicelink"]` discriminator, `api_base` default `https://app.voicelink.co.in/api`, optional `username`/`password`/`bearer_token`, **required `did_number`**, `from_numbers: List[str]`, optional `client_id`; validator requires `bearer_token` OR (`username` AND `password`); response masks sensitive fields.
4. **`serializers.py`** — `VoiceLinkFrameSerializer` (A-law 8 kHz, `{"event":"media","stream_sid","media":{"payload"}}`, `{"event":"clear"}` on interruption, DTMF deserialize, `VOICELINK_INBOUND_GAIN`).
5. **`transport.py`** — `create_transport(...)` calling `load_credentials_for_transport(..., expected_provider="voicelink")` and returning a `FastAPIWebsocketTransport` wired to the serializer (`voicelink_sample_rate=8000`, `sample_rate=audio_config.pipeline_sample_rate`).
6. **`provider.py`** — `VoiceLinkProvider(TelephonyProvider)` with `PROVIDER_NAME = WorkflowRunMode.VOICELINK.value`, `WEBHOOK_ENDPOINT="voicelink/events"`, `normalize_customer_number`, `_login`/`_api_request` (one 401 retry), `initiate_call` (build `websocket_url`/`events_url` from `get_backend_endpoints()`, `POST /v1/add_lead`), `parse_status_callback`, `handle_websocket`, `start_inbound_stream`, `validate_config`. (`transfer_call` → `NotImplementedError`.)
7. **`routes.py`** — module-level `router = APIRouter()`, `POST /voicelink/events/{workflow_run_id}` (parse → `_process_status_update`, return sentinel dicts, never raise), and `@router.websocket("/ws")` inbound (read `start`, route by DID via the `TelephonyConfigurationModel`+`TelephonyPhoneNumberModel` join, `_create_inbound_workflow_run`, `run_pipeline_telephony`). Use **lazy in-function imports** to avoid the circular import.
8. **`__init__.py`** — build `_UI_METADATA` (`display_name="VoiceLink"`, fields incl. `api_base` placeholder `https://app.voicelink.co.in/api`, `username`/`password`/`bearer_token` sensitive, `did_number`, `from_numbers`, `client_id`); build `SPEC = ProviderSpec(name="voicelink", provider_cls=VoiceLinkProvider, config_loader=_config_loader, transport_factory=create_transport, transport_sample_rate=8000, config_request_cls=..., config_response_cls=..., ui_metadata=_UI_METADATA, account_id_credential_field="username")`; call `register(SPEC)`. (Make `_config_loader` pass through `client_id`.)
9. **Register at startup.** Add `voicelink` to the import tuple in `api/services/telephony/providers/__init__.py`. **This is the only edit outside the provider folder needed for registration** (routes auto-mount; factory/audio_config/run_pipeline are registry-driven).
10. **Schema union.** In `api/schemas/telephony_config.py`: import the request/response, add the request to the `TelephonyConfigRequest` discriminated `Union`, add `voicelink: Optional[VoiceLinkConfigurationResponse] = None` to `TelephonyConfigurationResponse`, add both to `__all__`.
11. **Set the WSS host.** Set env var **`BACKEND_API_ENDPOINT`** to a public `https://` origin (e.g. `https://api.example.com`). Verify `get_backend_endpoints()` yields `wss://api.example.com`. Do **not** rely on the localhost default (yields plain `ws://` + tunnel fallback).
12. **Reverse proxy.** Ensure TLS termination + WebSocket upgrade pass-through for `/api/v1/telephony/ws` (Caddy/nginx). No TURN needed.
13. **DB data rows** (no migration needed if the models/columns already exist): one `telephony_configurations` row (`provider="voicelink"`, JSONB config with `api_base` + creds + `did_number`); one or more `telephony_phone_numbers` rows (`address_normalized` = the DID, `is_active=true`, `inbound_workflow_id` set) so inbound DID routing resolves.
14. **UI.** No per-provider UI code — VoiceLink appears in the provider dropdown automatically once metadata is registered. Add the config via Settings → Telephony → Add telephony configuration; add DIDs/inbound-workflow binding on the config detail page.
15. **VoiceLink portal side.** Register the bare `wss://<your-host>/api/v1/telephony/ws` as the inbound bot/stream URL. Outbound requires no portal config.
16. **(Optional) Reseller KYC/provisioning feature.** If using it: add `api/services/voicelink_kyc/`, `api/services/voicelink_clients/`, `api/utils/secret_crypto.py`; run the two Alembic migrations (`c9e2f5a17d04_...`, `a7f3c1e9b2d6_...`); set `VOICELINK_RESELLER_USERNAME/PASSWORD`, `VOICELINK_API_BASE`, and a Fernet `APP_SECRET_KEY` (and/or `VOICELINK_PROVISION_KEY`).
17. **Tests.** Port `api/tests/telephony/voicelink/{test_provider,test_serializer,test_routes}.py` and keep the mocked seams (`_api_request`/`_send_request`/`_login`, `get_backend_endpoints`, `db_client`, `get_telephony_provider_for_run`, `_process_status_update`) intact. Run the suite to confirm the contracts in §7.
18. **Verify live.** Place an outbound call (confirm the logged `add_lead` `websocket_url` and that VoiceLink connects back to `/ws/{...}`), then an inbound call (read the logged raw `start` frame; correct the unconfirmed DID/answer-key guesses and add a regression test).

---

## Missing / unverified slices (stated explicitly)

- **Inbound is implemented but unverified.** The exact inbound `start`-frame DID/caller field names and the inbound answer-body keys are flagged "unconfirmed upstream" in the code and mirror `add_lead` as a best guess. `parse_inbound_webhook` even comments that inbound is "not currently routed through VoiceLink," yet a full inbound `/ws` path exists. Treat inbound as working-but-needs-a-real-call to confirm.
- **No per-call REST status/cost; no transfers.** `get_call_status` → `"unknown"`, `get_call_cost` → zeros, `transfer_call` → `NotImplementedError`.
- **Webhook authenticity** is not verifiable (unsigned; signature checks are no-ops).
- The findings did **not** include the verbatim full bodies of `transport.py`, `__init__.py`'s `_UI_METADATA` block, or the two Alembic migrations — those are described by role/fields but not quoted line-for-line here.
- The referenced design spec (`docs/superpowers/specs/2026-06-16-admin-clients-voicelink-status-and-create-design.md`) describes the **reseller-provisioning** feature only and marks inbound + media-WSS **out of scope**; the single-WSS / inbound+outbound design intent is authoritative **only in code docstrings** (`provider.py` header, `routes.py` `voicelink_inbound_ws`).