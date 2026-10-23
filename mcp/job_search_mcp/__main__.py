"""Entry point.

    job-search-mcp              run the MCP server on stdio (what Claude Code and Codex launch)
    job-search-mcp list-tools   print the registered tool names as JSON (in-process, no transport)
    job-search-mcp selftest     launch this server as a stdio subprocess, handshake, list its tools,
                                print {"ok": ..., "tools": [...]} as JSON and exit 0 only when every
                                expected tool answered (used by scripts/connectors_doctor.py)

Only list-tools and selftest print to stdout; when serving, stdout belongs to the JSON-RPC
transport and every diagnostic goes to stderr.
"""
from __future__ import annotations

import asyncio
import json
import sys


def _list_tools() -> list[str]:
    from .server import mcp

    return sorted(t.name for t in asyncio.run(mcp.list_tools()))


async def _selftest(timeout: float = 30.0) -> dict:
    from mcp import Client, StdioServerParameters

    from .server import READ_TOOLS, WRITE_TOOLS

    params = StdioServerParameters(command=sys.executable, args=["-m", "job_search_mcp"])
    async with Client(params, read_timeout_seconds=timeout) as client:
        tools = sorted(t.name for t in (await client.list_tools()).tools)
    expected = sorted(READ_TOOLS + WRITE_TOOLS)
    missing = sorted(set(expected) - set(tools))
    return {"ok": not missing, "tools": tools, "missing": missing, "transport": "stdio"}


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "list-tools":
        print(json.dumps({"tools": _list_tools()}))
        return
    if cmd == "selftest":
        try:
            result = asyncio.run(asyncio.wait_for(_selftest(), timeout=60))
        except Exception as exc:  # noqa: BLE001 - the doctor needs a verdict, not a traceback
            result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        print(json.dumps(result))
        sys.exit(0 if result.get("ok") else 1)
    if cmd != "serve":
        print(f"unknown command {cmd!r}; use serve, list-tools or selftest", file=sys.stderr)
        sys.exit(2)
    from .server import main as serve

    serve()


if __name__ == "__main__":
    main()
