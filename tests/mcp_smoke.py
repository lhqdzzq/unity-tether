"""MCP smoke test: connect to the MCP server over stdio and list/call tools.

Run without Unity to verify tool registration; run with Unity for an end-to-end
tool call (ping).
"""

import asyncio
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

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools ({len(names)}):")
            for n in names:
                print("  -", n)

            expected = {"ping", "get_state", "play", "stop", "execute_csharp", "screenshot", "create_object", "find_objects", "modify_object", "delete_object", "get_scene", "load_prefab", "save_prefab", "instantiate_prefab", "open_prefab", "close_prefab", "create_folder", "create_asset", "move_asset", "delete_asset", "list_assets", "run_tests"}
            missing = expected - set(names)
            assert not missing, f"missing tools: {missing}"

            # Call ping — requires the live bridge.
            try:
                result = await session.call_tool("ping", {})
                print("ping ->", result.content)
            except Exception as exc:
                print("ping failed (bridge not up?):", exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
