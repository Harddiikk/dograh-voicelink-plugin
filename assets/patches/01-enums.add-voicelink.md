# Patch 01 — add the `VOICELINK` enum member

**File:** `api/enums.py`
**Why:** `WorkflowRunMode.VOICELINK` is the single load-bearing provider string. It
is the registry key, the DB `provider` column value, `VoiceLinkProvider.PROVIDER_NAME`,
and the importlib router-discovery key. Without it the package raises `AttributeError`
on import.

> `scripts/apply_overlay.py` applies this automatically. Use this file only if the
> script reported `✗ could not find 'class WorkflowRunMode'` (an unusual layout).

## Change

Add one member to the `WorkflowRunMode` enum. Placement inside the class does not
matter — anywhere among the members is fine.

```diff
 class WorkflowRunMode(Enum):
     ARI = "ari"
     PLIVO = "plivo"
     TWILIO = "twilio"
     VONAGE = "vonage"
     VOBIZ = "vobiz"
+    VOICELINK = "voicelink"
     CLOUDONIX = "cloudonix"
     TELNYX = "telnyx"
     WEBRTC = "webrtc"
```

## Notes

- **No Alembic / DB enum migration is required.** Dograh stores `provider` as a plain
  string column, so adding the Python enum member is sufficient.
- Keep the value exactly `"voicelink"` (lowercase). It must match the package
  directory name `providers/voicelink/` and `ProviderSpec(name="voicelink")`.
