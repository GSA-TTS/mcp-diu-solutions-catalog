# Hosting the DIU Solutions Catalog MCP

For an NVIDIA DGX-1 with host nginx, use [deploy/dgx/compose.yaml](deploy/dgx/compose.yaml)
with [nginx-ip-port.conf](deploy/dgx/nginx-ip-port.conf) for a trusted network or
[nginx-https.conf.example](deploy/dgx/nginx-https.conf.example) for public HTTPS.

The hosted server exposes MCP Streamable HTTP at `/mcp`, a liveness check at `/health`,
and a data/model readiness check at `/ready`. It is stateless at the MCP transport layer and
uses one process per container because embedded Qdrant takes an exclusive lock on its local files.

## Private cloud.gov hosting through the GSA hub

Use the isolated [cloud.gov Docker deployment kit](deploy/cloudgov/README.md) to build
and publish the existing image to GHCR, deploy an immutable digest with an internal-only
route, and authorize Obot with a port-scoped C2C policy. No Python buildpack is used.
The external client connects through Obot at `/mcp-connect/<vmcp-id>` without a `/mcp`
suffix; it does not connect directly to a public DIU application route.
If Docker cannot download the model through an enterprise TLS proxy, the kit's
optional `--local-model` mode imports an approved workstation cache and rebuilds
the index offline in Linux. It does not disable certificate verification.

## Run locally over HTTP

```bash
uv sync --extra dev
uv run diu-catalog-web
```

Then verify:

```bash
curl http://localhost:8001/health
curl http://localhost:8001/ready
```

The MCP URL is `http://localhost:8001/mcp`. Test it with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector@latest
```

Select **Streamable HTTP** and enter `http://localhost:8001/mcp`.

## Run with Docker

The image build installs the locked dependencies, downloads the embedding model, and creates the
137-record Qdrant index. The resulting image is self-contained and does not download anything when
it starts.

```bash
docker build -t diu-catalog-mcp .
docker run --rm -p 8001:8001 diu-catalog-mcp
```

Or:

```bash
docker compose up --build
```

## Deploy to a container host

Deploy the `Dockerfile` to any platform that provides a stable public HTTPS URL, such as Cloud Run,
Fly.io, Railway, Render, Azure Container Apps, AWS App Runner, or a Kubernetes deployment.

Configure the platform as follows:

- Container port: `8001` (the server also honors the platform-provided `PORT` variable).
- Health check: `/health`.
- Readiness check, when supported: `/ready`.
- Process count per container: `1`; do not configure multiple Uvicorn workers.
- Public MCP endpoint: `https://YOUR-HOST/mcp`.
- Minimum memory: 512 MiB; 1 GiB is a comfortable default for ONNX embeddings.

Multiple container replicas are safe because each replica has its own read-only catalog snapshot,
model cache, and embedded Qdrant files.

## Connect from ChatGPT

OpenAI's current instructions require a publicly reachable HTTPS endpoint that supports MCP
Streamable HTTP. In ChatGPT:

1. Open **Settings**, select **Security and login**, and enable **Developer mode**.
2. Go to **ChatGPT Plugins**, select the plus button, and create a connection.
3. Enter the complete MCP URL: `https://YOUR-HOST/mcp`.
4. Review the seven discovered tools and test the connection in a new conversation.

The `search` and `fetch` tools implement OpenAI's compatibility schemas for company knowledge and
deep research. The five `*_solutions`/catalog tools provide richer catalog-specific exploration.
All tools are annotated as read-only and return canonical DIU URLs for citations.

Official references:

- [Connect and test your plugin in ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt/)
- [Build an MCP server](https://developers.openai.com/apps-sdk/build/mcp-server/)
- [Building MCP servers for plugins and API integrations](https://platform.openai.com/docs/mcp)

## Optional fixed-token protection

Because the DIU catalog is public and every tool is read-only, the default deployment does not
require authentication. For MCP clients that can send a fixed bearer token or `X-API-Key`, set:

```bash
DIU_MCP_API_KEY="generate-a-long-random-secret"
```

This protects `/mcp` while leaving `/health` and `/ready` available to the hosting platform.

Do not use a fixed token as a substitute for user authentication. If a future version reads private
data or adds write actions, configure OAuth 2.1 and enforce per-user authorization in the server.
OpenAI's ChatGPT documentation requires authentication discovery for account-bound tools.

## Connect another MCP client

Point any Streamable HTTP-compatible client at:

```text
https://YOUR-HOST/mcp
```

If `DIU_MCP_API_KEY` is enabled and the client supports headers, send either:

```text
Authorization: Bearer YOUR_SECRET
```

or:

```text
X-API-Key: YOUR_SECRET
```
