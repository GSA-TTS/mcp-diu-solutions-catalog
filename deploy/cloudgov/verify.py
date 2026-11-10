"""Real HTTP/MCP smoke checks; prints summaries, never record bodies or tokens."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any
from urllib.parse import urlsplit

TOOLS = {
    "search_solutions",
    "get_solution",
    "list_solutions",
    "catalog_facets",
    "search",
    "fetch",
}


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Expected an HTTP(S) MCP URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Credentials, queries, and fragments are not allowed in URLs")
    host = parsed.hostname
    if parsed.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"}:
        if not host.endswith(".apps.internal"):
            raise ValueError("HTTP is allowed only for loopback or approved CF internal routes")
    return value.rstrip("/")


def payload(result: Any) -> Any:
    if result.is_error:
        raise RuntimeError("MCP tool returned an error")
    if result.structured_content is not None:
        value = result.structured_content
        return value.get("result", value) if isinstance(value, dict) else value
    texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
    if len(texts) != 1:
        raise ValueError("Expected one JSON tool response")
    return json.loads(texts[0])


async def verify_tools(client: Any, query: str) -> dict[str, Any]:
    discovered = {tool.name for tool in await client.list_tools()}
    if not TOOLS.issubset(discovered):
        raise ValueError("Required product tools are missing")
    facets = payload(await client.call_tool("catalog_facets", {}))
    if not isinstance(facets, dict) or not facets.get("portfolios"):
        raise ValueError("Catalog facets are empty or invalid")
    page = payload(await client.call_tool("list_solutions", {"limit": 1}))
    if not isinstance(page, dict) or page.get("total", 0) < 1 or not page.get("solutions"):
        raise ValueError("Catalog browsing returned no records")
    matches = payload(await client.call_tool("search_solutions", {"query": query, "limit": 1}))
    if not isinstance(matches, list) or not matches or not matches[0].get("uuid"):
        raise ValueError("Semantic search returned no record IDs")
    record = payload(await client.call_tool("get_solution", {"uuid": matches[0]["uuid"]}))
    if not isinstance(record, dict) or record.get("uuid") != matches[0]["uuid"]:
        raise ValueError("Semantic search/retrieval IDs do not match")
    search = payload(await client.call_tool("search", {"query": query}))
    if not isinstance(search, dict) or not search.get("results"):
        raise ValueError("Compatibility search returned no results")
    identifier = search["results"][0]["id"]
    fetched = payload(await client.call_tool("fetch", {"id": identifier}))
    if not isinstance(fetched, dict) or fetched.get("id") != identifier or not fetched.get("text"):
        raise ValueError("Compatibility search/fetch failed")
    return {"tools": sorted(TOOLS), "catalog_records": page["total"]}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from diu_catalog_mcp.tls import configure_system_trust

    configure_system_trust()
    url = validate_url(args.mcp_url)
    token = os.getenv("DIU_VERIFY_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    ready_count = None
    if args.check_health:
        if urlsplit(url).path != "/mcp":
            raise ValueError("Health checks require a direct component URL ending in /mcp")
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
            for path, expected in (("/health", "ok"), ("/ready", "ready")):
                response = await http.get(url.removesuffix("/mcp") + path)
                response.raise_for_status()
                body = response.json()
                if body.get("status") != expected:
                    raise ValueError("Unexpected health/readiness status")
                if path == "/ready":
                    ready_count = body.get("indexed_records")
                    if not isinstance(ready_count, int) or ready_count < 1:
                        raise ValueError("Index is empty or invalid")
    transport = StreamableHttpTransport(url, headers=headers)
    async with Client(transport, timeout=60) as client:
        summary = await verify_tools(client, args.query)
    if ready_count is not None and ready_count != summary["catalog_records"]:
        raise ValueError("CSV and index counts differ")
    return {"status": "PASS", "health_checked": args.check_health, **summary}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--check-health", action="store_true")
    parser.add_argument("--query", default="resilient communications")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), sort_keys=True))
    except Exception as exc:
        # HTTP/MCP exceptions can contain headers, URLs, or raw /ready errors.
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
