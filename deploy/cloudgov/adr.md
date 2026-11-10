---
title: Use the existing Docker image for private cloud.gov hosting
description: Isolate cloud.gov deployment configuration while retaining the upstream image build.
status: proposed
tier: 2
date: 2026-11-09
last_updated: 2026-11-09
decision_makers: [Repository maintainers]
category: Deployment and Infrastructure
nist_controls: [AC-6, CM-3, CM-6, SC-7, SA-11, SR-3]
impact_level: pending-agency-assessment
ato_relevance: yes-boundary
risk_treatment: mitigate
related_files: [deploy/cloudgov/README.md, deploy/cloudgov/manifest.yml, deploy/cloudgov/build-and-push.sh, deploy/cloudgov/Dockerfile.local-model, Dockerfile]
---

# Use the existing Docker image for private cloud.gov hosting

## Context and Problem Statement

The GSA MCP hub requires a private cloud.gov component behind its authenticated
Obot gateway. The DIU fork already builds a self-contained Docker image; a second
Python buildpack packaging pipeline would increase maintenance across the fork network.

## Decision Drivers

- Reuse the existing locked dependency, model, and index build.
- Mirror the NIH RePORTER publishing kit: read the project version, build
  `linux/amd64`, and push version plus `latest` tags in one command.
- Isolate platform-specific configuration and avoid application code changes.
- Restrict network access to Obot using a port-scoped C2C policy.
- Deploy immutable image digests and verify real semantic search.

## Considered Options

1. Existing Docker image with isolated CF manifest (selected by the requesting maintainer).
2. Python buildpack plus a separate asset-packaging pipeline.
3. Platform-specific derivative image (only if compatibility testing requires it).

## Decision Outcome

Use option 1, subject to target-platform Docker support and live compatibility
verification. This record remains proposed pending human review; no deployment
or ATO approval is implied. Agency impact classification is not yet established.

### Positive Consequences

- No duplicate image build or changes to DGX/local deployment.
- The shared publishing convention is isolated under `deploy/cloudgov/`;
  deployment still pins a published digest rather than a mutable tag.
- Approved optional local-model mode imports a locally downloaded cache,
  regenerates the index in Linux with networking disabled, and uses FastEmbed's
  `HF_HUB_OFFLINE=1` support at runtime. No application or TLS changes are needed.
- Model and catalog snapshot travel together; rollback selects a previous digest.
- Runtime uses instance-local storage without a separate database service.

### Negative Consequences

- Image pulls, staging disk, runtime-user permissions, and offline model loading
  must be verified in the actual target space.
- The local-model Dockerfile duplicates root dependency/runtime stages to avoid
  modifying upstream files. A focused parity test detects unintended divergence.
  Cache provenance and integrity must be reviewed; dependency downloads still need TLS.
- Catalog refreshes require image rebuilds. One worker owns each Qdrant index.
- Existing `/ready` raw exception output remains unchanged by request. Keep
  diagnostic output private; remediation requires a later development-team proposal.

### Compliance Consequences

- C2C policy and private routing support AC-6/SC-7 but do not replace an ATO.
- Digest pinning and human-reviewed changes support CM-3/SR-3.
- Capture live routing, health, gateway, and external-client checks for SA-11.
- Review image vulnerabilities, model provenance/licensing, internal-hostname
  disclosure, and authorization-boundary documentation before production.

## Links

- [Deployment runbook](README.md)
- [Existing image build](../../Dockerfile)
- [Hosting guidance](../../DEPLOYMENT.md)
