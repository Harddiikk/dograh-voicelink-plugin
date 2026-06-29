# Patch 02 — register the provider package

**File:** `api/services/telephony/providers/__init__.py`
**Why:** Dograh's provider system is registry-driven. Importing the `voicelink`
package triggers `register(SPEC)` at module load. This one line is the **only edit
outside the provider folder required for registration** — routes auto-mount, and the
factory / audio_config / run_pipeline are all registry-driven (no per-provider edits).

> `scripts/apply_overlay.py` applies this automatically. Use this file only if the
> script reported `✗ registration import tuple not found`.

## Change

Add `voicelink` to the side-effect import tuple (alphabetical order is conventional
but not required):

```diff
 from api.services.telephony.providers import (  # noqa: F401  -- import for side effects (registration)
     ari,
     cloudonix,
     plivo,
     telnyx,
     twilio,
     vobiz,
+    voicelink,
     vonage,
 )
```

## Notes

- The trailing comment on the `import (` line contains `(registration)` — note the
  parens. Do **not** insert based on the first `)` you find; insert before the lone
  closing-paren line. (The script handles this line-by-line.)
- If your Dograh version lists providers differently (e.g. a list literal or explicit
  `import` statements), just ensure `api.services.telephony.providers.voicelink` is
  imported somewhere at startup so `register(SPEC)` runs.
