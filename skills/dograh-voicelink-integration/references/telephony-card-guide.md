# The Settings → Telephony card (VoiceLink)

How VoiceLink shows up in the Dograh UI and exactly what to enter. The card is
**fully metadata-driven** — the frontend (`ui/src/components/telephony/ConfigFormDialog.tsx`)
has zero per-provider code; it renders fields from backend metadata. Once the overlay is
installed, **VoiceLink appears in the provider dropdown automatically**. No UI build is
needed.

## Opening the card

**Settings → Telephony / Phone numbers → Add telephony configuration.** Pick a name
(e.g. "VoiceLink prod"), then choose **VoiceLink** from the provider dropdown (locked once
editing an existing config).

## Fields (what the card renders for VoiceLink)

These come from `_UI_METADATA` in `providers/voicelink/__init__.py`:

| Field | Type | Required | Placeholder / note |
|---|---|---|---|
| **API Base URL** | text | no | `https://app.voicelink.co.in/api` — VoiceLink's REST API base (dial/login). **NOT the WSS URL.** Defaults to this if left blank. |
| **Username** | password (masked) | no* | VoiceLink account username. Provide username+password so expired tokens auto-refresh. |
| **Password** | password (masked) | no* | VoiceLink account password. |
| **Bearer Token** | password (masked) | no* | Static token; optional when username+password are set (no auto-refresh on expiry). |
| **DID Number** | text | **yes** | Your DID in registered form, e.g. `919484959244`. Used as the outbound caller id. |
| **Phone Numbers** | string-array | no | `from_numbers` — defined in metadata but **not rendered by the add-config form**. Manage DIDs on the config detail page instead (see below). |
| **Client ID** | text | no | VoiceLink client id, passed through to the outbound `add_lead` call's `provider_metadata`. ⚠️ A known `_config_loader` gap currently drops it before `add_lead` — see `debugging.md`. |

\* **Credential rule:** the config requires **either** a `bearer_token` **or** both
`username` **and** `password`. Saving with neither is rejected.

> There is deliberately **no WSS-URL field** here. The media WSS URL is derived from
> `BACKEND_API_ENDPOINT` and pasted into the VoiceLink portal, not into this card. See
> `single-wss-url.md`.

## Save flow

The form `POST`s `createTelephonyConfigurationApiV1OrganizationsTelephonyConfigsPost`
(or `PUT …/{id}` on edit) with body:
```json
{ "name": "VoiceLink prod", "is_default_outbound": true,
  "config": { "provider": "voicelink", "api_base": "...", "username": "...",
              "password": "...", "did_number": "919484959244", "from_numbers": ["..."] } }
```
The `config` is validated against the discriminated union the overlay wired into
`api/schemas/telephony_config.py` (dispatch on `provider: "voicelink"`). If that schema
edit is missing, the save 422s — re-run the overlay / apply patch 03.

Tick **"Set as default for outbound calls"** if this should be the org's default outbound
provider.

## DIDs and inbound binding (required for inbound calls)

Phone numbers are managed separately from credentials. After saving the config, open its
detail page (`/telephony-configurations/[configId]`) and use **Add phone number** to:

1. Add each DID (it gets normalized; country hint `IN`).
2. **Bind the DID to an inbound workflow** (`inbound_workflow_id`).

This creates the `telephony_phone_numbers` row that the inbound `/ws` handler routes
against — **the DID is the inbound authorization boundary** (VoiceLink's inbound frame
carries no account id). Without an active row bound to a workflow, inbound calls close
with `4404`.

## After the card

1. Make sure `BACKEND_API_ENDPOINT` is your public https origin (so the derived WSS URL
   is `wss://`).
2. Paste `wss://<host>/api/v1/telephony/ws` into the VoiceLink portal (inbound bot URL).
3. Test: outbound first (check the `add_lead` log), then inbound (check the `start` frame
   log).
