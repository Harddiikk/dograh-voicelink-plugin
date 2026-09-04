# Patch 03 — wire VoiceLink into the telephony config schema

**File:** `api/schemas/telephony_config.py`
**Why:** This module assembles the discriminated union that the API uses to parse
`POST/PUT /telephony-configs` bodies (dispatching on the `provider` literal). Without
this wiring, saving a VoiceLink configuration from the Settings → Telephony card is
rejected by Pydantic, and the response shape lacks the `voicelink` field.

> `scripts/apply_overlay.py` applies these edits automatically and idempotently
> — three of them always, and (c) only when the target file's schema shape
> calls for it (see below). Use this file only if the script reported a `✗`
> for this file; a `=` line for (c) saying "not applicable" is expected and
> fine on current upstream.

## Four edits

### (a) Import the request/response classes

```diff
 from api.services.telephony.providers.vonage.config import (
     VonageConfigurationRequest,
     VonageConfigurationResponse,
 )
+from api.services.telephony.providers.voicelink.config import (
+    VoiceLinkConfigurationRequest,
+    VoiceLinkConfigurationResponse,
+)
```

### (b) Add the request to the discriminated union

```diff
 TelephonyConfigRequest = Annotated[
     Union[
         ARIConfigurationRequest,
         CloudonixConfigurationRequest,
         PlivoConfigurationRequest,
         TelnyxConfigurationRequest,
         TwilioConfigurationRequest,
         VobizConfigurationRequest,
+        VoiceLinkConfigurationRequest,
         VonageConfigurationRequest,
     ],
     Field(discriminator="provider"),
 ]
```

### (c) Add the response field — only on the older schema shape

**Skip this edit on current upstream** (2026-09-04, `dograh-hq/dograh@b1fc4e51`
onward): the flat `TelephonyConfigurationResponse` class was removed —
credentials are now masked generically into
`TelephonyConfigurationDetail.credentials` (a plain `dict`), and no provider's
`__init__.py` wires a response class into `ProviderSpec` any more (that field
was dropped from `ProviderSpec` itself; passing `config_response_cls=...` now
raises `TypeError` at construction, before `register(SPEC)` ever runs).
`scripts/apply_overlay.py` detects which shape the target file has and applies
this edit only when the class exists — on the newer shape it reports the step
as "not applicable" and moves on, rather than failing the whole overlay.

If you *are* looking at an older Dograh checkout that still has the class,
apply it as before:

```diff
 class TelephonyConfigurationResponse(BaseModel):
     """Top-level telephony configuration response. ..."""

     twilio: Optional[TwilioConfigurationResponse] = None
     plivo: Optional[PlivoConfigurationResponse] = None
     vonage: Optional[VonageConfigurationResponse] = None
     vobiz: Optional[VobizConfigurationResponse] = None
+    voicelink: Optional[VoiceLinkConfigurationResponse] = None
     cloudonix: Optional[CloudonixConfigurationResponse] = None
     ari: Optional[ARIConfigurationResponse] = None
     telnyx: Optional[TelnyxConfigurationResponse] = None
```

### (d) Export from `__all__`

```diff
 __all__ = [
     ...
     "VobizConfigurationRequest",
     "VobizConfigurationResponse",
+    "VoiceLinkConfigurationRequest",
+    "VoiceLinkConfigurationResponse",
     "VonageConfigurationRequest",
     "VonageConfigurationResponse",
 ]
```

## Notes

- The response field must be a real class attribute — make sure it lands **after** the
  class docstring, not inside it. Only applies when `TelephonyConfigurationResponse`
  exists at all — see (c) above.
- Some Dograh versions plan to "move to metadata-driven forms" and may already use a
  flatter union; in that case just ensure `VoiceLinkConfigurationRequest` is reachable
  by the discriminator on `provider="voicelink"`.
- This patch only covers the *config-schema* side. The provider itself (WS URL,
  `handle_websocket`, `_create_inbound_workflow_run`, `validate_phone_number`) has its
  own upstream-compat notes in `references/integration-map.md` — current upstream
  scopes the media WebSocket by `organization_id`, not `user_id`, and requires
  `validate_phone_number` on every provider.
