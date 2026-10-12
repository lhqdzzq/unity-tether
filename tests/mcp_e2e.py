"""MCP E2E test: drive the Unity bridge through MCP tool calls (requires Unity running)."""

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def main() -> int:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "unity_tether.mcp_server"],
        cwd=str(REPO_ROOT),
        env=env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # text tool: ping
            ping = await session.call_tool("ping", {})
            assert "pong" in ping.content[0].text, ping.content
            print("ping ->", ping.content[0].text)

            # text tool: create_object
            created = await session.call_tool("create_object", {"name": "McpCube", "primitive": "Cube", "position": [0, 1, 0]})
            obj = json.loads(created.content[0].text)
            print("created ->", obj)
            obj_id = obj["id"]

            # image tool: screenshot
            shot = await session.call_tool("screenshot", {"width": 160, "height": 120})
            kinds = [c.type for c in shot.content]
            print("screenshot content kinds:", kinds)
            image = next(c for c in shot.content if c.type == "image")
            assert image.mime_type == "image/png", image.mime_type
            assert len(image.data) > 100, "image data too small"
            print("screenshot image base64 length:", len(image.data))

            # cleanup
            deleted = await session.call_tool("delete_object", {"obj": obj_id})
            print("deleted ->", deleted.content[0].text)

    print("PASS: MCP end-to-end (text + image tools) verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
