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

These come from `_UI_METADATA` in `providers/voicelink/__init__.py`. The card is
**credentials only** — three fields:

| Field | Type | Required | Note |
|---|---|---|---|
| **Username** | text (masked) | no* | VoiceLink account username. Provide username+password so expired tokens auto-refresh. |
| **Password** | password (masked) | no* | VoiceLink account password. |
| **Bearer Token** | password (masked) | no* | Static token; optional when username+password are set (no auto-refresh on expiry). |

\* **Credential rule:** the config requires **either** a `bearer_token` **or** both
`username` **and** `password`. Saving with neither is rejected.

> **No API Base URL field.** `api_base` still exists in the schema and defaults to
> `https://app.voicelink.co.in/api` (VoiceLink's REST base for dial/login). It is not
> exposed on the card; override it in the stored `config` JSONB directly if a different
> base is ever needed.

> **No DID / Phone Numbers field.** The outbound caller id is supplied per call (the
> `from_number` passed to `initiate_call`), and inbound DIDs are managed as
> `telephony_phone_numbers` rows on the config detail page (see below) — not on this card.
> VoiceLink's `add_lead` still **requires** a `did_number`, so a DID must be bound to the
> campaign for outbound to work — `initiate_call` raises `ValueError` if none is passed.

> There is deliberately **no WSS-URL field** here. The media WSS URL is derived from
> `BACKEND_API_ENDPOINT` and pasted into the VoiceLink portal, not into this card. See
> `single-wss-url.md`.

## Save flow

The form `POST`s `createTelephonyConfigurationApiV1OrganizationsTelephonyConfigsPost`
(or `PUT …/{id}` on edit) with body:
```json
{ "name": "VoiceLink prod", "is_default_outbound": true,
  "config": { "provider": "voicelink", "username": "...", "password": "..." } }
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
