"""MCP server for Unity Tether.

Exposes the bridge's semantic methods as Model Context Protocol tools so an AI
assistant (Claude, Cursor, …) can control the Unity Editor. Runs over stdio.

The server holds one bridge connection and reconnects once on failure (Unity
restarts or Domain Reload drop the TCP link — see the C3 invariant).
"""

from __future__ import annotations

import base64

from mcp.server.mcpserver import Image, MCPServer

from .client import TetherError, UnityTetherClient

server = MCPServer(
    "unity-tether",
    version="0.1.0",
    instructions=(
        "Control and inspect the Unity Editor: build scenes, edit prefabs and "
        "assets, run C#, capture screenshots, and read runtime logs."
    ),
)

_client: UnityTetherClient | None = None


def _get_client() -> UnityTetherClient:
    global _client
    if _client is None:
        _client = UnityTetherClient().connect()
    return _client


def _call(method: str, params: dict | None = None) -> object:
    global _client
    try:
        return _get_client().call(method, params)
    except (OSError, TetherError):
        # Reconnect once: the bridge drops on Unity restart / Domain Reload.
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = UnityTetherClient().connect()
        return _client.call(method, params)


# -- debug / state ----------------------------------------------------------
@server.tool()
def ping() -> dict:
    """Check that the Unity bridge is reachable."""
    return _call("ping")


@server.tool()
def get_state() -> dict:
    """Get the editor's current state (edit/play/paused, compiling, scene, Unity version)."""
    return _call("state")


@server.tool()
def play() -> dict:
    """Enter play mode."""
    return _call("play")


@server.tool()
def stop() -> dict:
    """Exit play mode."""
    return _call("stop")


@server.tool()
def execute_csharp(type_name: str, method_name: str, args: list | None = None) -> dict:
    """Invoke any public static C# method by reflection.

    Example: type_name='UnityEngine.Debug', method_name='Log', args=['hello'].
    """
    return _call("call", {"type": type_name, "method": method_name, "args": args or []})


@server.tool()
def screenshot(width: int = 800, height: int = 600) -> Image:
    """Capture a screenshot of the current Unity view and return it as an image."""
    result = _call("screenshot", {"width": width, "height": height})
    png = base64.b64decode(result["base64"])
    return Image(data=png, format="png")


# -- scene / object control -------------------------------------------------
@server.tool()
def create_object(
    name: str = "GameObject",
    primitive: str | None = None,
    components: list | None = None,
    position: list | None = None,
    rotation: list | None = None,
    scale: list | None = None,
    parent: int | None = None,
) -> dict:
    """Create a GameObject (optionally a primitive with components) and return its id/path.

    `primitive` is one of Sphere/Capsule/Cylinder/Cube/Plane/Quad; `position` is [x,y,z];
    `parent` is the instance id of the parent GameObject.
    """
    return _call(
        "create_object",
        {
            "name": name,
            "primitive": primitive,
            "components": components,
            "position": position,
            "rotation": rotation,
            "scale": scale,
            "parent": parent,
        },
    )


@server.tool()
def find_objects(name: str | None = None, root: int | None = None) -> dict:
    """Find GameObjects by name (optionally within a prefab root given by instance id)."""
    return _call("find_objects", {"name": name, "root": root})


@server.tool()
def modify_object(
    obj: int,
    position: list | None = None,
    rotation: list | None = None,
    scale: list | None = None,
    active: bool | None = None,
    name: str | None = None,
) -> dict:
    """Modify a GameObject (by instance id): position/rotation/scale, active state, or name."""
    return _call(
        "modify_object",
        {"object": obj, "position": position, "rotation": rotation, "scale": scale, "active": active, "name": name},
    )


@server.tool()
def delete_object(obj: int) -> dict:
    """Delete a GameObject by instance id."""
    return _call("delete_object", {"object": obj})


@server.tool()
def get_scene() -> dict:
    """Return the active scene summary (name, root objects)."""
    return _call("get_scene", {})


# -- prefab control ---------------------------------------------------------
@server.tool()
def load_prefab(path: str) -> dict:
    """Load a prefab asset's contents for editing; returns the root object's instance id."""
    return _call("load_prefab", {"path": path})


@server.tool()
def save_prefab(obj: int, path: str) -> dict:
    """Save a GameObject (scene object or loaded prefab root) as a prefab asset."""
    return _call("save_prefab", {"object": obj, "path": path})


@server.tool()
def instantiate_prefab(path: str) -> dict:
    """Instantiate a prefab asset into the active scene."""
    return _call("instantiate_prefab", {"path": path})


# -- asset control ----------------------------------------------------------
@server.tool()
def create_folder(path: str) -> dict:
    """Create a folder under Assets (e.g. 'Assets/Prefabs')."""
    return _call("create_folder", {"path": path})


@server.tool()
def create_asset(path: str, asset_type: str = "material") -> dict:
    """Create an asset (type: 'material' or 'prefab') at the given path."""
    return _call("create_asset", {"path": path, "type": asset_type})


@server.tool()
def move_asset(src: str, dst: str) -> dict:
    """Move or rename an asset."""
    return _call("move_asset", {"src": src, "dst": dst})


@server.tool()
def delete_asset(path: str) -> dict:
    """Delete an asset."""
    return _call("delete_asset", {"path": path})


@server.tool()
def list_assets(folder: str = "Assets", recursive: bool = False) -> dict:
    """List asset paths in a folder."""
    return _call("list_assets", {"folder": folder, "recursive": recursive})


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
