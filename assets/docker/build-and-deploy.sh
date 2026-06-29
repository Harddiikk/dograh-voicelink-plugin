#!/usr/bin/env bash
#
# Build the VoiceLink overlay image and (optionally) deploy it.
#
# This wraps the proven production overlay flow: derive a custom image from the stock
# Dograh API image with the VoiceLink provider baked in, then point your
# compose at it. Safe to re-run.
#
# Usage:
#   build-and-deploy.sh [options]
#
# Options:
#   --base IMAGE       Stock base image       (default: dograhai/dograh-api:latest)
#   --tag  IMAGE       Output image tag        (default: local/dograh-api:voicelink)
#   --dograh-root P    Code root in the image  (default: auto-detect)
#   --push             docker push the built image
#   --compose-up FILE  After build, run: docker compose -f FILE up -d api
#   -h, --help         Show this help
#
# Examples:
#   ./build-and-deploy.sh
#   ./build-and-deploy.sh --tag ghcr.io/<you>/dograh-api:voicelink --push
#   ./build-and-deploy.sh --compose-up /opt/dograh/docker-compose.yml
set -euo pipefail

BASE="dograhai/dograh-api:latest"
TAG="local/dograh-api:voicelink"
DOGRAH_ROOT=""
PUSH=0
COMPOSE_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --base)        BASE="$2"; shift 2 ;;
    --tag)         TAG="$2"; shift 2 ;;
    --dograh-root) DOGRAH_ROOT="$2"; shift 2 ;;
    --push)        PUSH=1; shift ;;
    --compose-up)  COMPOSE_FILE="$2"; shift 2 ;;
    -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Resolve the plugin root = two levels up from this script (assets/docker/..).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOCKERFILE="$PLUGIN_ROOT/assets/docker/Dockerfile.voicelink-overlay"

echo "→ Building VoiceLink overlay"
echo "    base   : $BASE"
echo "    tag    : $TAG"
echo "    context: $PLUGIN_ROOT"
[ -n "$DOGRAH_ROOT" ] && echo "    root   : $DOGRAH_ROOT (explicit)"

BUILD_ARGS=(--build-arg "BASE_IMAGE=$BASE")
[ -n "$DOGRAH_ROOT" ] && BUILD_ARGS+=(--build-arg "DOGRAH_ROOT=$DOGRAH_ROOT")

docker build -f "$DOCKERFILE" -t "$TAG" "${BUILD_ARGS[@]}" "$PLUGIN_ROOT"
echo "✓ Built $TAG"

if [ "$PUSH" = "1" ]; then
  echo "→ Pushing $TAG"
  docker push "$TAG"
  echo "✓ Pushed"
fi

if [ -n "$COMPOSE_FILE" ]; then
  echo "→ Restarting the compose 'api' service"
  echo "  NOTE: this script does NOT edit your compose file. The 'api' service"
  echo "  must already use image: $TAG (set it directly, or via the REGISTRY/TAG"
  echo "  vars your compose supports) or the restart will run the OLD image."
  docker compose -f "$COMPOSE_FILE" up -d api
  # Verify the running container actually uses the overlay image — don't claim
  # success on a restart of the old image.
  running="$(docker compose -f "$COMPOSE_FILE" images api 2>/dev/null | awk 'NR>1{print $4":"$5}' | head -1)"
  if printf '%s' "$running" | grep -q "$(printf '%s' "$TAG" | sed 's/.*\///')"; then
    echo "✓ api is running the overlay image ($running)"
  else
    echo "⚠️  api is NOT running $TAG (got: ${running:-unknown})."
    echo "    Edit the compose 'api' service to use image: $TAG, then re-run with --compose-up."
  fi
  echo "  Now run scripts/verify.sh against your public API URL."
fi

cat <<EOF

Next steps:
  1. Ensure the compose 'api' service uses image: $TAG
  2. Set BACKEND_API_ENDPOINT to your public https:// origin in .env.api
  3. docker compose up -d api
  4. Verify:  scripts/verify.sh https://<your-api-domain>
EOF
