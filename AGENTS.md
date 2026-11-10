# Agent guide: DIU Solutions Catalog MCP

## Purpose

This repository packages the public [DIU Commercial Solutions Catalog](https://www.diu.mil/solutions/portfolio/catalog)
as a read-only MCP server. Its goal is to make successful DIU prototypes easier to discover by
mission need, capability, vendor, DIU focus area, or completion year.

The checked-in data is a point-in-time public catalog snapshot, not a live proxy to DIU. At the
time this guide was written, `data/diu_solutions_catalog.csv` contains 137 records. Do not treat
that count as a durable API contract: refreshes can change it.

## Repository map

```text
.
├── src/diu_catalog_mcp/
│   ├── server.py        FastMCP definition and the seven MCP tools
│   ├── web.py           Streamable-HTTP ASGI application, health/readiness, optional API key
│   ├── scraper.py       DIU API scraper and atomic CSV writer
│   ├── vector_store.py  Embedded Qdrant storage, FastEmbed integration, ingestion CLI
│   ├── models.py        CatalogRecord and ChatGPT-compatible response models
│   ├── config.py        Environment-derived paths and model settings
│   ├── tls.py           Operating-system trust-store helpers
│   └── __init__.py      Package metadata and Hugging Face downloader setting
├── data/diu_solutions_catalog.csv  Versioned catalog snapshot (source data)
├── tests/               Unit and in-process MCP/HTTP tests; no model download is required
├── Dockerfile           Self-contained production image build
├── compose.yaml         Simple local Docker Compose deployment
├── deploy/dgx/
│   ├── compose.yaml                 DGX-oriented Compose deployment
│   ├── .env.example                 Bind address, port, and optional MCP token template
│   ├── nginx-ip-port.conf           nginx reverse proxy for a trusted-network HTTP endpoint
│   └── nginx-https.conf.example     nginx reverse proxy template for public HTTPS
├── README.md            Concise project overview and local usage
├── DEPLOYMENT.md        Hosting, health-check, auth, and client-connection guidance
├── pyproject.toml       Package metadata, dependencies, scripts, pytest, and Ruff configuration
└── uv.lock              Locked Python dependencies; keep it in sync with pyproject.toml
```

Generated model and Qdrant directories are `data/models` and `data/qdrant`; they are ignored by
Git. The Docker image creates both during its build. Do not add either generated directory to the
repository.

## Application architecture

There is one shared `FastMCP` object, defined in `src/diu_catalog_mcp/server.py`.

```text
catalog CSV ──> ingestion ──> embedded Qdrant collection
                                  │
MCP stdio ────────────────────────┤
Streamable HTTP /mcp ──> FastMCP ─┘
```

`server.py` provides the domain behavior, while `web.py` is only the HTTP transport wrapper:

- `diu-catalog-mcp` calls `mcp.run()` and uses FastMCP's default stdio transport.
- `diu-catalog-web` creates a stateless Streamable-HTTP app with the MCP route at `/mcp` and runs
  it with one Uvicorn worker on `HOST` (default `0.0.0.0`) and `PORT` (default `8001`).
- `GET /` provides service metadata; `GET /health` is liveness; `GET /ready` verifies that the
  configured Qdrant collection is present and nonempty.

Embedded Qdrant is an on-disk library used in the same process, not an independently deployed
database or an exposed database port. It takes an exclusive lock on its local files, so use one
Uvicorn worker per container. Separate container replicas are fine because each has separate local
storage.

### MCP tools and response boundaries

All seven tools are marked read-only, non-destructive, idempotent, and closed-world.

| Tool | Behavior |
| --- | --- |
| `search_solutions` | Semantic search with optional exact `portfolio`, `vendor`, and `year_completed` filters; returns full public record fields plus a score. |
| `get_solution` | Looks up one complete public record by DIU UUID. |
| `list_solutions` | Browses public records with exact filters and pagination. |
| `catalog_facets` | Returns portfolios, vendors, and years with record counts. |
| `catalog_status` | Returns configured local paths, model/collection names, and CSV/index counts. |
| `search` | ChatGPT company-knowledge/deep-research-compatible search returning stable IDs, titles, and canonical DIU URLs. |
| `fetch` | ChatGPT-compatible full retrieval for an ID returned by `search`. |

`CatalogRecord` owns the CSV schema and search document construction. The indexed text includes the
project, vendor, portfolio, year, DoD partners, key terms, problem, and solution. The public MCP
payload intentionally excludes raw HTML fields and `scraped_at`; preserve that distinction unless
the public tool contract is intentionally changed.

The server caches settings, records, and vector-store initialization with `lru_cache`. A running
process will not automatically notice a replaced CSV, changed index, or changed relevant
environment variable. Restart it after a refresh or configuration change.

## Data lifecycle

The authoritative runtime inputs are the configured CSV, embedded Qdrant directory, embedding
model, and model cache. Defaults are relative to the working directory:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DIU_CATALOG_CSV` | `data/diu_solutions_catalog.csv` | Catalog CSV source snapshot |
| `DIU_QDRANT_PATH` | `data/qdrant` | Embedded Qdrant storage path |
| `DIU_COLLECTION` | `diu_solutions` | Qdrant collection name |
| `DIU_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | FastEmbed text embedding model |
| `DIU_MODEL_CACHE` | `data/models` | FastEmbed/Hugging Face model cache |

The CSV fields, in order, are:

```text
uuid, project_name, vendor_name, portfolio, year_completed, dod_partners,
key_terms, problem, solution, key_terms_html, problem_html, solution_html,
source_url, scraped_at
```

### Refresh procedure

1. Run `uv run diu-catalog-scrape --output data/diu_solutions_catalog.csv`.
2. Inspect the CSV diff and record count before committing public-data changes.
3. Run `uv run diu-catalog-ingest --csv data/diu_solutions_catalog.csv --db data/qdrant` for a
   local server, or rebuild the Docker image for a container deployment.
4. Restart a local server, or redeploy the rebuilt image, then check `/ready`.

Scraping uses DIU's summary and detail APIs, requires the summary count to match the received
records, fetches details concurrently (default 12), retries request failures, strips HTML into the
plain-text discovery fields, sorts records by extracted year descending then project name, and
atomically replaces the output CSV after a successful scrape. Keep those completeness and
atomic-write protections when modifying the scraper.

Ingestion creates embeddings with FastEmbed and writes public-record payloads to Qdrant under a
stable integer ID derived from each UUID. A rebuild replaces the configured collection. It is a
destructive operation for that local collection; do not point it at an index that must be retained
without a backup or a deliberate rebuild plan.

Scraping does **not** rebuild the index, and starting the web server does **not** scrape or ingest.
This separation is intentional: refreshes must explicitly update both the CSV and its matching
index. The server rejects an empty or missing index on first store use.

`HF_HUB_DISABLE_XET=1` is set early by the package to use the standard Hugging Face downloader,
which is more compatible with enterprise TLS proxies. Do not move the setting below imports that
may initialize Hugging Face libraries. TLS requests use the host operating system trust store via
`truststore`.

## Development workflow

Python 3.10+ is supported; the Docker image uses Python 3.12. Use `uv` and retain the lockfile.

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

The tests are deliberately offline:

- scraper tests use mocked `httpx` responses;
- vector tests inject a deterministic in-memory embedder;
- MCP tests exercise the in-process FastMCP object with a fake store;
- web tests use Starlette's `TestClient` and verify optional API-key behavior.

Run the full test suite and Ruff after changes to Python code. For a documentation-only edit,
check internal Markdown links and use `git diff --check`.

When adding or changing a tool, update `server.py`, its focused tests, and the user-facing tool
list in `README.md`. Preserve read-only annotations unless the product contract explicitly changes.
When changing configuration, update `config.py`, Docker environment defaults where applicable,
and `README.md`/`DEPLOYMENT.md` together.

## Docker and hosting

The multi-stage `Dockerfile` is the production artifact:

1. The builder installs the locked non-development environment, copies the source and checked-in
   CSV, then runs ingestion to download the model and create the Qdrant index.
2. The runtime image copies that prepared `/app` directory, exposes port `8001`, and starts
   `.venv/bin/diu-catalog-web`.

Consequently, image builds need outbound access to package and model sources, but ordinary container
starts use the included CSV, model cache, and index. A catalog or embedding-model change requires
an image rebuild; no runtime volume is required by the supplied Compose files.

Use `docker compose up --build` at the repository root for direct port `8001` access. For a DGX or
host-nginx setup, `deploy/dgx/compose.yaml` binds port `8001` to `127.0.0.1` by default; nginx is
then the network-facing reverse proxy. Its trusted-network template serves HTTP on port `8099`; the
HTTPS template is the appropriate starting point for public exposure. Keep the Docker container
private to the host when nginx is used.

For any public client, the MCP URL is `https://HOST/mcp`. Configure `/health` for liveness and
`/ready` for readiness. Never configure multiple Uvicorn workers for this embedded-Qdrant design.

### cloud.gov deployment kit

For cloud.gov hosting behind the GSA hub, use [deploy/cloudgov/README.md](deploy/cloudgov/README.md)
and its Docker manifest. Keep this kit isolated from the root Dockerfile and DGX deployment.
Deploy immutable image digests on an internal-only route, verify the CF-selected container port,
and scope C2C access to Obot on that port. User-facing hub manifests live in the hub repository.
The existing `/ready` error behavior is intentionally unchanged by this kit.
The kit's optional `--local-model` build imports an ignored model cache using a named
BuildKit context. Keep `Dockerfile.local-model` aligned with the root Dockerfile;
its intentional differences are cache import, offline flags, and network-disabled ingestion.
FastEmbed's `HF_HUB_OFFLINE=1` prevents runtime model downloads in that variant.

### Optional API key

Setting `DIU_MCP_API_KEY` protects requests whose path begins with `/mcp`. Clients may send either
`Authorization: Bearer TOKEN` or `X-API-Key: TOKEN`. `/`, `/health`, and `/ready` remain unauthenticated
so hosting platforms can perform health checks. This fixed token is appropriate only for this
read-only public-data service; do not extend it as a substitute for OAuth and per-user authorization
if private data or write tools are introduced.

## Documentation and change discipline

- Keep [README.md](README.md) concise: purpose, architecture summary, paths to run the service, and
  data-pipeline basics.
- Put hosting, reverse-proxy, health-check, and authentication detail in
  [DEPLOYMENT.md](DEPLOYMENT.md).
- The `deploy/dgx/` directory intentionally contains configuration files, not a second README.
  Keep any DGX-specific usage context in `DEPLOYMENT.md` or in comments adjacent to the config.
- Do not claim a fixed catalog count or imply that the catalog automatically refreshes.
- Do not publish credentials, generated `.env` files, local model caches, Qdrant data, or unrelated
  binary artifacts.

Before changing deployment behavior, trace the effect across `Dockerfile`, root `compose.yaml`,
`deploy/dgx/compose.yaml`, both nginx templates, `web.py`, and `DEPLOYMENT.md`. Before changing the
data model, trace it across the scraper, `CatalogRecord`, vector indexing, MCP tool outputs, tests,
and the checked-in CSV.
