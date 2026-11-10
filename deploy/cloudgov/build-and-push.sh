#!/bin/bash
# Build the existing DIU image and push version + latest tags to GHCR.
# Used by the private cloud.gov component behind the Obot MCP gateway.
# MCP: :8001/mcp; health: :8001/health (verify CF's assigned port).
#
# Prerequisites: Docker with Buildx and an approved GHCR login with write:packages.
# Usage: bash deploy/cloudgov/build-and-push.sh [--local-model] [--load]
# Publishing requires approval; use a new pyproject.toml version for each release.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

REGISTRY="ghcr.io"
IMAGE="${REGISTRY}/gsa-tts/mcp-diu-solutions-catalog"

LOCAL_MODEL=false
OUTPUT="--push"
for argument in "$@"; do
  case "$argument" in
    --local-model) LOCAL_MODEL=true ;;
    --load) OUTPUT="--load" ;;
    *) echo "FATAL: unknown argument: $argument" >&2; exit 1 ;;
  esac
done
BUILD_ARGS=(--file Dockerfile)
if [[ "$LOCAL_MODEL" == true ]]; then
  MODEL_CACHE="${DIU_LOCAL_MODEL_CACHE:-${REPO_ROOT}/deploy/cloudgov/build/models}"
  if [[ ! -d "$MODEL_CACHE" ]]; then
    echo "FATAL: local model cache directory is missing; download the model first" >&2
    exit 1
  fi
  MODEL_CACHE="$(cd "$MODEL_CACHE" && pwd)"
  BUILD_ARGS=(--file deploy/cloudgov/Dockerfile.local-model)
  BUILD_ARGS+=(--build-context "local_models=${MODEL_CACHE}")
fi

# Read only [project].version, not a dependency or tooling version.
VERSION="$(awk '
  /^\[project\][[:space:]]*$/ { project = 1; next }
  /^\[/ { project = 0 }
  project && /^version[[:space:]]*=/ {
    if (match($0, /"[^"]+"/)) print substr($0, RSTART + 1, RLENGTH - 2)
    exit
  }
' pyproject.toml)"
if [[ ! "$VERSION" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "FATAL: could not read a valid Docker version tag from [project].version" >&2
  exit 1
fi
REVISION="$(git rev-parse HEAD)"

echo "Building ${IMAGE}:${VERSION} and :latest (linux/amd64, ${OUTPUT})"
# Match the other server kits: single-shot cross-architecture build + push.
docker buildx build \
  --platform linux/amd64 \
  "${BUILD_ARGS[@]}" \
  --label org.opencontainers.image.source=https://github.com/GSA-TTS/mcp-diu-solutions-catalog \
  --label "org.opencontainers.image.revision=${REVISION}" \
  --label "org.opencontainers.image.version=${VERSION}" \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  "$OUTPUT" \
  .

if [[ "$OUTPUT" == "--load" ]]; then
  echo "Loaded ${IMAGE}:${VERSION} and ${IMAGE}:latest locally; nothing was pushed."
  exit 0
fi
echo "Pushed ${IMAGE}:${VERSION} and ${IMAGE}:latest"
echo "Inspect the published digest: docker buildx imagetools inspect ${IMAGE}:${VERSION}"
echo "Deploy the immutable digest, not :latest. Retain the previous digest for rollback."
echo "On first push, review package contents and image-pull requirements."
echo "For anonymous CF pulls: GitHub -> Org packages -> mcp-diu-solutions-catalog"
echo "  -> Package settings -> Change visibility -> Public (authorized owner only)."
