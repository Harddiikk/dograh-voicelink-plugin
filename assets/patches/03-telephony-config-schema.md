# Patch 03 — wire VoiceLink into the telephony config schema

**File:** `api/schemas/telephony_config.py`
**Why:** This module assembles the discriminated union that the API uses to parse
`POST/PUT /telephony-configs` bodies (dispatching on the `provider` literal). Without
this wiring, saving a VoiceLink configuration from the Settings → Telephony card is
rejected by Pydantic, and the response shape lacks the `voicelink` field.

> `scripts/apply_overlay.py` applies all four edits below automatically and
> idempotently. Use this file only if the script reported a `✗` for this file.

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

### (c) Add the response field

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
  class docstring, not inside it.
- Some Dograh versions plan to "move to metadata-driven forms" and may already use a
  flatter union; in that case just ensure `VoiceLinkConfigurationRequest` is reachable
  by the discriminator on `provider="voicelink"`.
