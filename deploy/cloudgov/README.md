---
title: Docker deployment kit for cloud.gov
description: Publish the existing DIU image and deploy it privately behind the Obot gateway.
status: draft
tier: 2
last_updated: 2026-11-09
related_files: [Dockerfile, DEPLOYMENT.md, deploy/cloudgov/manifest.yml, deploy/cloudgov/vars.example.yml, deploy/cloudgov/build-and-push.sh, deploy/cloudgov/Dockerfile.local-model, deploy/cloudgov/adr.md, tests/test_cloudgov_kit.py]
---

# Docker deployment kit for cloud.gov

This kit reuses the [root Dockerfile](../../Dockerfile) to avoid maintaining a
second packaging pipeline in the fork. It does not use a Python buildpack,
change application code, or modify local/DGX hosting. The hub catalog entries
belong in the separate `mcp-server-hub-catalog` repository.

**Status:** locally and live unverified. Human review is required before deployment.
cloud.gov platform authorization does not authorize this application for production.
See the [decision record](adr.md) for rationale and review requirements.

## Prerequisites

- Docker with Buildx, GHCR package publishing permission, and CF CLI v8.
- Approval to download base images, packages, and embedding model artifacts,
  and to publish this public-data image to the selected registry.
- Target cloud.gov space permits Docker deployment and can pull from GHCR.
- Adequate image/staging disk and memory quota; the manifest starts at 4 GiB
  disk, 1 GiB memory, and one instance. Measure and adjust with approval.
- An approved internal hostname and Obot app identity for the C2C policy.
- Review the image's runtime-user compatibility and writable Qdrant/model paths.
  The optional local-cache Dockerfile does not change runtime permissions. Stop if CF rejects the
  current image or its runtime cannot access the index; do not patch permissions blindly.

## 1. Check local tools and Python

From the repository root:

```bash
git status --short
cat .python-version
python3 --version
uv --version
docker version
docker buildx version
cf version
```

The Docker build uses Python 3.12 regardless of your host Python. For local tests,
use the project's selected interpreter. These commands may download Python and
dependencies; run them only with approval for those downloads:

```bash
uv python install 3.12
uv sync --frozen --extra dev --python 3.12
uv run --no-sync python --version
uv run --no-sync pytest
uv run --no-sync ruff check .
git diff --check
```

The kit's dependency-free unit tests can also run without uv or model downloads:

```bash
python3 -m unittest discover -s tests -p test_cloudgov_kit.py -v
```

These tests do not replace real-image, live CF, or gateway verification. Run your
approved secrets, dependency/license, and container vulnerability scanners before
publishing. No scanner or CI workflow is installed by this kit.

## 2. Optionally build and test locally before publishing

The primary publishing command is [build-and-push.sh](build-and-push.sh), matching
the NIH RePORTER kit's project-version, `linux/amd64`, and version-plus-`latest`
convention. It remains here to isolate fork-specific deployment files. It builds
and pushes in one step; this local build is optional but recommended before
publishing to verify real offline behavior.

Choose a new `pyproject.toml` project version for each release; the script overwrites
existing tags and does not check for prior releases. Build from
a reviewed clean revision (the revision label alone does not describe uncommitted changes).
GSA-TTS organization permissions may require a package owner to create/link the
package before your first push.

```bash
export IMAGE=ghcr.io/gsa-tts/mcp-diu-solutions-catalog
# Set TAG to the [project].version value shown here:
grep '^version' pyproject.toml
export TAG=0.1.0
export REVISION=$(git rev-parse HEAD)
docker buildx build --platform linux/amd64 --load \
  --label org.opencontainers.image.source=https://github.com/GSA-TTS/mcp-diu-solutions-catalog \
  --label org.opencontainers.image.revision="$REVISION" \
  --label org.opencontainers.image.version="$TAG" \
  --tag "$IMAGE:$TAG" .
docker image inspect "$IMAGE:$TAG" --format '{{.Os}}/{{.Architecture}} ports={{json .Config.ExposedPorts}} user={{json .Config.User}}'
```

`--load` makes the single-platform image available for local checks before push.
The build downloads the model and creates the index from the checked-in CSV;
it does **not** scrape DIU. An Apple Silicon host builds `linux/amd64` through
Docker emulation, which may be slower.

### Local-model mode for enterprise TLS build failures

If your workstation can securely download the model but Docker cannot, download
locally using the approved network and normal certificate verification:

```bash
uv run --no-sync diu-catalog-ingest \
  --csv data/diu_solutions_catalog.csv \
  --db deploy/cloudgov/build/local-qdrant \
  --model-cache deploy/cloudgov/build/models
```

The generated directories are ignored. The local index is disposable and is
replaced on repeated ingestion; the Linux build regenerates its own index. The
unauthenticated Hugging Face warning does not require a token for this public model.

Build and load locally, without publishing:

```bash
bash deploy/cloudgov/build-and-push.sh --local-model --load
```

This selects [Dockerfile.local-model](Dockerfile.local-model) and imports only the
model cache through a named BuildKit context. Preserve the complete cache, including
refs, snapshots, blobs, and symlinks. Do not point the context at your home directory,
credentials, or an unrelated cache; everything in that directory is copied into
the image. For an alternative approved cache directory, set `DIU_LOCAL_MODEL_CACHE`
to its path (resolved relative to the repository root if not absolute).

FastEmbed 0.8.0 recognizes `HF_HUB_OFFLINE=1`, which this variant sets at build
and runtime. Ingestion uses `RUN --network=none`, so an incomplete cache fails
instead of falling back to downloads. Base-image, OS-package, and Python dependency
downloads still need network access and valid TLS; this is not an entirely offline
image build. Neither certificates nor TLS verification are changed.

Record the model repository and revision in the cache's `refs/main`, and retain
checksums/provenance with your release evidence. Use the cache matching the selected
embedding model. Do not refresh it between testing and publishing.

Run the offline runtime checks below against the locally loaded version tag.
After those and security scans pass, authenticate to GHCR and publish with:

```bash
bash deploy/cloudgov/build-and-push.sh --local-model
```

This rebuilds and pushes version plus `latest` tags. To publish the exact image
you tested instead, use the manual version-tag push described in section 4.
The standard no-argument workflow is unchanged. `--load` is also supported without
`--local-model` for ordinary local smoke builds.

## 3. Test without runtime outbound networking

Start an isolated container, without publishing host ports. Its loopback remains
usable for checks executed inside it. Do not run this concurrently against a
shared mounted Qdrant directory; the supplied image needs no data volume.

```bash
docker run --detach --network none --name diu-cloudgov-smoke "$IMAGE:$TAG"
docker cp deploy/cloudgov/verify.py diu-cloudgov-smoke:/app/cloudgov_verify.py
docker exec diu-cloudgov-smoke .venv/bin/python /app/cloudgov_verify.py \
  --mcp-url http://127.0.0.1:8001/mcp --check-health
docker restart diu-cloudgov-smoke
docker exec diu-cloudgov-smoke .venv/bin/python /app/cloudgov_verify.py \
  --mcp-url http://127.0.0.1:8001/mcp --check-health
```

Wait for startup before executing the check after each start/restart. A `PASS`
checks health, nonempty index, CSV/index count agreement, six tool calls, semantic
search/retrieval, and compatibility search/fetch. Inspect container logs privately
on failure; the helper deliberately does not print response bodies or raw errors.

Cleanup only the disposable test container when finished:

```bash
docker stop diu-cloudgov-smoke
docker rm diu-cloudgov-smoke
```

## 4. Authenticate and push to GHCR

Use your GitHub username, **not** the organization name. Use an approved GitHub
personal access token (classic) with `write:packages` for publishing and access to
the package; authorize it for organization SSO if required. Do not paste the token
into a command, manifest, vars file, or chat. Docker may store credentials in its
configured credential helper; confirm that helper is approved for your workstation.

In Bash:

```bash
read -r -p 'GitHub username: ' GHCR_USER
read -r -s -p 'GHCR token: ' GHCR_TOKEN
printf '\n'
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io --username "$GHCR_USER" --password-stdin
unset GHCR_TOKEN
```

Stop if login fails. After local checks and approved security scans pass, publish:

```bash
bash deploy/cloudgov/build-and-push.sh
```

The script reads `[project].version` itself, ignores the shell's TAG variable, and
pushes both the version and `latest` tags. Use `--local-model` if the builder must
use the downloaded cache; omitting it selects the root Dockerfile. After success,
inspect the published digest (set TAG to the current project version):

```bash
export IMAGE=ghcr.io/gsa-tts/mcp-diu-solutions-catalog
grep '^version' pyproject.toml
export TAG=0.1.0
docker buildx imagetools inspect "$IMAGE:$TAG"
```

The script rebuilds for publishing. If you need to publish the exact locally tested
image instead, use `docker push "$IMAGE:$TAG"` **instead of** the script. That
alternative publishes only the local version tag, not `latest`.

Stop if login or push fails. Check package permissions, SSO authorization, and
the `write:packages` scope rather than adding broad permissions. For Zsh, enter
`bash` first or use its own hidden-input syntax.

Copy the published **Digest** (`sha256:...`) from the push/inspection output.
Use `$IMAGE@sha256:...` for deployment, not a mutable tag. Verify that the registry
manifest has the expected `linux/amd64` platform.

GHCR packages are normally private initially. For anonymous CF pulls, an authorized
package owner must deliberately set the package visibility to **Public** in GitHub
package settings after reviewing the image contents. A public source repository
does not automatically make its image public. Alternatively, use the target
platform's approved private-registry procedure; never commit registry credentials.

Test anonymous access after changing visibility (this creates a disposable Docker
configuration without your normal login credentials):

```bash
ANON_CONFIG=$(mktemp -d)
DOCKER_CONFIG="$ANON_CONFIG" docker pull "$IMAGE@sha256:REPLACE_WITH_DIGEST"
rmdir "$ANON_CONFIG"
```

If your Docker setup requires a credential helper even in that configuration,
have an unauthenticated CI/CF environment verify the pull instead. Retain the
previous known-good digest for rollback. Log out when appropriate:

```bash
docker logout ghcr.io
```

## 5. Deploy privately to cloud.gov

Deployment and network-policy changes need operator approval. Confirm Docker
support and approved image sources with the platform team before running these.
Copy [vars.example.yml](vars.example.yml) to `vars.local.yml`, replace all three
values, and keep the local file untracked. `image` must be a digest reference.

```bash
cp deploy/cloudgov/vars.example.yml deploy/cloudgov/vars.local.yml
cf login -a https://api.fr.cloud.gov --sso
cf target -o YOUR_ORG -s YOUR_SPACE
cf push -f deploy/cloudgov/manifest.yml \
  --vars-file deploy/cloudgov/vars.local.yml --no-start
cf app YOUR_APP_NAME
cf start YOUR_APP_NAME
cf app YOUR_APP_NAME
```

The manifest maps only `apps.internal` and preserves the image command. It does
not set `PORT`: CF supplies it and the application already honors it. The existing
image exposes port `8001`; verify CF's selected listening port before setting the
policy and catalog URL. Do not assume the hub examples' `8080` applies here.
The Dockerfile health check also assumes `8001`; CF uses the manifest's `/health`
check independently.

For an **existing** app, inspect routes and remove any previous public mapping
through the approved operator process before starting. A manifest alone is not
proof that stale routes or old network policies have been removed.

Where SSH is already permitted, inspect the runtime-assigned port:

```bash
cf ssh YOUR_APP_NAME -c 'printenv PORT'
```

If SSH is unavailable, ask the platform operator to verify the port; do not enable
SSH or broaden access merely to bypass a blocked check. Grant only the verified
port from the actual Obot app (specify source/destination spaces/orgs when needed):

```bash
cf add-network-policy YOUR_OBOT_APP --destination-app YOUR_APP_NAME \
  --protocol tcp --port VERIFIED_PORT
cf network-policies
```

From inside Obot, check the approved internal URL's `/health` and `/ready`. Check
that no public application route exists and unauthorized source apps cannot
connect. Do not rely on an Internet DNS failure as proof of policy isolation.

The Qdrant/model directories are instance-local. One worker is required; independent
instances are possible but must be load-tested before increasing the instance count.
No runtime ingestion, refresh task, persistent volume, nginx, or external Qdrant
service is provisioned.

## 6. Verify the hub product

The hub needs a remote `multiUser` internal component and a paired `type: vmcp`
product with stable identifiers. Its `fixedURL` uses the approved internal route,
verified port, and `/mcp`. Authorize these six tools explicitly:
`search_solutions`, `get_solution`, `list_solutions`, `catalog_facets`, `search`,
and `fetch`. Exclude `catalog_status` from ordinary-user access because it reports
absolute local paths. Do not add a per-user token configuration for this shared
public snapshot; Obot handles authentication and product permissions.

With a real external client's approved OAuth flow or scoped API key, connect to:

```text
https://<obot-host>/mcp-connect/<vmcp-id>
```

Do not append `/mcp`. The helper can also check that URL using an approved scoped
API key in `DIU_VERIFY_TOKEN` (enter it with hidden input); omit `--check-health`
because Obot's root health endpoint is not the component's readiness endpoint:

```bash
read -r -s -p 'Scoped Obot API key: ' DIU_VERIFY_TOKEN
printf '\n'
export DIU_VERIFY_TOKEN
uv run --no-sync python deploy/cloudgov/verify.py \
  --mcp-url https://YOUR_OBOT_HOST/mcp-connect/YOUR_VMCP_ID
unset DIU_VERIFY_TOKEN
```

Gateway tool-name rewriting may require adaptation to the verified deployed
Obot contract; never interpret a missing-tool failure as a pass. Separately check
that `catalog_status` is denied/not advertised to the normal product audience.
The helper is not a replacement for connecting the intended real external client.

## Verification, refresh, and rollback

Record UTC timestamp, operator, source revision, image digest, commands, observed
outputs, and PASS/FAIL/BLOCKED for:

- Unit tests, Ruff, Markdown links, YAML validation, and whitespace checks.
- Approved secrets, dependency/license, model-provenance, and image security scans.
- Real image startup, offline embeddings, restart, and runtime-user permissions.
- CF resources, selected port, private routes, C2C allow/deny, health/readiness.
- Hub schema/reconciliation, tool policy, gateway calls, and external-client connection.

Do not record tokens, government queries, raw response bodies, or private topology
in public PR artifacts. The existing `/ready` can return raw exception details;
sanitization is explicitly outside this kit's scope and requires a later proposal.

To refresh, review a newly scraped CSV, rebuild the root image, repeat checks, push
a new tag, and deploy its digest. Scraping is not performed by this kit. Restarting
does not refresh the snapshot. For rollback, restore the previous image digest in
the ignored local vars file and repeat `cf push` and verification. Coordinate with
the hub maintainer to revert/disable the product listing if needed. Do not delete
local data or perform an in-place production index rebuild as a rollback shortcut.

For fork maintenance, keep platform files here, reuse upstream entry points,
and rerun compatibility checks after upstream image changes. The local-model
Dockerfile mirrors root dependency/runtime stages; the focused tests flag drift
outside its cache import, offline flags, and network-disabled ingestion. No generated assets,
registry credentials, or CI/CD workflows belong in this directory.
