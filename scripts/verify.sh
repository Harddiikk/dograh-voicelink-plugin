#!/usr/bin/env bash
#
# Verify a VoiceLink ↔ Dograh install end-to-end. Read-only; safe to run anytime.
#
# Checks, in order:
#   1. API health         GET  <base>/api/v1/health           → 200
#   2. WSS upgrade probe   WS   <base>/api/v1/telephony/ws      → 101 Switching Protocols
#        (the BARE /ws path is the VoiceLink-specific INBOUND route — it only
#         exists if the voicelink package registered and its routes auto-mounted,
#         so a 101 here is strong proof the overlay is live. No auth required.)
#   3. Providers metadata  GET  <base>/api/v1/organizations/telephony/providers/metadata
#        (optional — needs a bearer token; pass --token to enable. Confirms
#         'voicelink' shows up in the Settings → Telephony provider dropdown.)
#
# It also prints the single WSS URL (inbound bare + outbound templated) derived
# from <base>, which is what you paste into the VoiceLink portal.
#
# Usage:
#   verify.sh <base-url> [--token <bearer>]
#   verify.sh https://api.auto4you.in
#   verify.sh http://localhost:8000 --token "$ACCESS_TOKEN"
set -uo pipefail

BASE="${1:-http://localhost:8000}"
TOKEN=""
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --token) TOKEN="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
BASE="${BASE%/}"

# ws(s) scheme derivation — mirrors api/utils/common.py::get_backend_endpoints
case "$BASE" in
  https://*) WSS="wss://${BASE#https://}" ;;
  http://*)  WSS="ws://${BASE#http://}"  ;;
  *) echo "base-url must start with http:// or https://" >&2; exit 2 ;;
esac

PASS=0; FAIL=0
ok(){ echo "  ✅ $1"; PASS=$((PASS+1)); }
bad(){ echo "  ❌ $1"; FAIL=$((FAIL+1)); }
warn(){ echo "  ⚠️  $1"; }

echo "VoiceLink ↔ Dograh verification → $BASE"
echo

# ---- 1. health ----
echo "[1/3] API health"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/api/v1/health" || echo 000)"
if [ "$code" = "200" ]; then ok "GET /api/v1/health → 200"; else bad "GET /api/v1/health → $code (expected 200)"; fi

# ---- 2. WSS upgrade probe on the bare inbound /ws ----
echo "[2/3] Single WSS endpoint (inbound bare /ws) upgrade probe"
KEY="$(openssl rand -base64 16 2>/dev/null || echo ZGVhZGJlZWZkZWFkYmVlZg==)"
# --http1.1 forces HTTP/1.1: Caddy serves HTTP/2 by default, over which a manual
# Upgrade handshake can never return 101 (would be a false negative).
resp="$(curl -s -i -N --http1.1 --max-time 6 \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: $KEY" \
  "$BASE/api/v1/telephony/ws" 2>&1 || true)"
# anchor to the status line so a stray "101 " in a header/body can't false-PASS
if printf '%s' "$resp" | grep -qiE '^HTTP/[0-9.]+ 101'; then
  ok "WS upgrade on /api/v1/telephony/ws → 101 (voicelink inbound route is live)"
elif printf '%s' "$resp" | grep -qiE "404|not found"; then
  bad "WS /api/v1/telephony/ws → 404 — voicelink routes not mounted (overlay not applied / not restarted)"
elif printf '%s' "$resp" | grep -qiE "426|upgrade required"; then
  warn "Got 426 Upgrade Required — route exists; the proxy may be stripping the WS upgrade. Check Caddy/nginx."
else
  warn "No clear 101 from /api/v1/telephony/ws. Reverse proxy may not pass WebSocket upgrades, or TLS/host is off."
  printf '%s\n' "$resp" | head -5 | sed 's/^/      /'
fi

# ---- 3. providers metadata (optional, needs token) ----
echo "[3/3] Providers metadata (voicelink in the telephony card)"
META="$BASE/api/v1/organizations/telephony/providers/metadata"
if [ -n "$TOKEN" ]; then
  body="$(curl -s --max-time 10 -H "Authorization: Bearer $TOKEN" "$META" || true)"
  if printf '%s' "$body" | grep -qi '"voicelink"\|VoiceLink'; then
    ok "metadata lists voicelink (it will appear in Settings → Telephony)"
  else
    bad "metadata did NOT list voicelink (response below)"
    printf '%s\n' "$body" | head -3 | sed 's/^/      /'
  fi
else
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$META" || echo 000)"
  if [ "$code" = "401" ] || [ "$code" = "403" ]; then
    warn "metadata endpoint needs auth ($code). Re-run with --token <bearer> to confirm voicelink is listed."
  else
    warn "metadata endpoint returned $code without a token; pass --token to verify voicelink presence."
  fi
fi

echo
echo "── The single WSS URL (one URL, both directions) ──────────────────────────"
echo "  Inbound  (paste into the VoiceLink portal as the bot/stream URL):"
echo "      $WSS/api/v1/telephony/ws"
echo "  Outbound (Dograh sends this to VoiceLink automatically per call):"
echo "      $WSS/api/v1/telephony/ws/{workflow_id}/{user_id}/{workflow_run_id}"
echo "  Call events webhook (also automatic):"
echo "      $BASE/api/v1/telephony/voicelink/events/{workflow_run_id}"
case "$WSS" in
  ws://*) warn "Base is http:// → derived ws:// (NOT wss://). For real calls, BACKEND_API_ENDPOINT must be a public https:// origin." ;;
esac

echo
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: $PASS check(s) passed, no failures. See /voicelink-debug if a call still fails."
  exit 0
fi
echo "RESULT: $FAIL failure(s), $PASS passed. Run /voicelink-debug for the troubleshooting playbook."
exit 1
