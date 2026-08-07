#!/usr/bin/env bash
set -euo pipefail

# Builds the image locally and pushes it, rather than using `gcloud run deploy
# --source .`. The hackathon temp accounts have roles/editor but not owner, so
# they cannot grant the default compute service account the permissions Cloud
# Build needs -- source deploys fail with PERMISSION_DENIED. Building locally
# never invokes Cloud Build, so it sidesteps that entirely.

PROJECT_ID="${PROJECT_ID:-cloudrun-hack26nyc-4309}"
REGION="${REGION:-us-east4}"
SERVICE="${SERVICE:-nyc-vision-agent}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE}:${TAG}"

# --platform linux/amd64 is required: Apple Silicon builds arm64 by default and
# Cloud Run will refuse the image.
docker build --platform linux/amd64 -t "$IMAGE" .
docker push "$IMAGE"

# The key is passed as an env var, never baked into the image. .env is
# gitignored and dockerignored.
ENV_ARGS="ARCHIVE_FRAMES=0"
if [ -f .env ]; then
  KEY=$(grep -E '^ROBOFLOW_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d "\"'" || true)
  [ -n "${KEY:-}" ] && ENV_ARGS="${ENV_ARGS},ROBOFLOW_API_KEY=${KEY}"
fi

# Cloud Run's filesystem is ephemeral and the service scales to zero, so frame
# archiving is off in the cloud -- run the poller locally to keep a real archive.
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --timeout 300 \
  --max-instances 5 \
  --min-instances 1 \
  --set-env-vars "$ENV_ARGS" \
  --quiet

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')
echo
echo "Deployed: $URL"
curl -sS "$URL/" && echo
