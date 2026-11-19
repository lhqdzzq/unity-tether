# unity-tether

> Tether the Unity Editor to your tools and your AI — a Python CLI for live log & debug control, plus an MCP server for AI-driven scene, prefab & asset control with screenshot feedback.

**unity-tether** is a bridge that connects the Unity Editor to external processes over a local TCP channel, exposing two front-ends on top of a single C# bridge:

| Front-end | Audience | Purpose |
|-----------|----------|---------|
| **CLI** (`tether`) | Humans & scripts | Live runtime logs, execute C#, screenshots, run tests — debug-oriented |
| **MCP server** | AI assistants (Claude, Cursor, …) | Semantic tools for building scenes, editing prefabs & assets, and visual verification |

One bridge, two handles: the CLI is the raw, debug-oriented handle; the MCP server is the semantic, AI-oriented handle. The hard parts — main-thread dispatch, async request/response correlation, and reconnect after Domain Reload — are shared by both.

## Architecture

```
Unity Editor
  └─ C# Bridge (TCP listener bound to 127.0.0.1)
       │  JSON · request-id · async
       ▼
  Python SDK (unity_client)
       ├── CLI        (tether logs, tether exec, tether screenshot, …)
       └── MCP server ── stdio ──► Claude / Cursor / other MCP clients
```

## Status

Working: **P0–P3 complete** (bridge, SDK, CLI, MCP server), plus P4 auto-reconnect and test runner. All features verified end-to-end against a real Unity editor.

## Features

**Bridge** (TCP on `127.0.0.1:8765`, newline JSON): 22 methods — `ping`, `state`, `play`, `stop`, `call`, `screenshot` (game view w/ IMGUI or camera render), `create_object`, `find_objects`, `modify_object`, `delete_object`, `get_scene`, `load_prefab`, `save_prefab`, `instantiate_prefab`, `open_prefab` / `close_prefab` (Prefab Stage editing), `create_folder`, `create_asset`, `move_asset`, `delete_asset`, `list_assets`, `run_tests` — plus log push events.

**CLI** (`tether`): `ping`, `state`, `play`, `stop`, `logs`, `call`, `screenshot`, `scene`, `find-objects`, `create-object`, `delete-object`, `set`, `load-prefab`, `save-prefab`, `instantiate-prefab`, `create-folder`, `create-asset`, `move-asset`, `delete-asset`, `list-assets`, `test`.

**MCP server** (`tether-mcp`): the same surface as semantic tools; `screenshot` returns an MCP image block for visual feedback.

## Roadmap

- **P0** — minimal bridge (TCP + main-thread dispatch + `ping` / `state`) ✅
- **P1** — debug loop (log forwarding, reflection `call`, screenshot) ✅
- **P2** — control (scene / object / prefab / asset) ✅
- **P3** — MCP server (20 tools + screenshot image feedback) ✅
- **P4** — engineering: auto-reconnect ✅ · test runner ✅ · Roslyn arbitrary-C# `exec` (deferred) · multi-instance routing (deferred)

## Install & quick start

**Unity side** — install the bridge into a Unity project:

1. Unity → **Window → Package Manager → + → Add package from disk…** and select this repo's `UnityBridge/` folder.
2. The bridge auto-starts and listens on `127.0.0.1:8765` (override via the `UNITY_TETHER_PORT` environment variable).
3. Confirm it is running via **Tools → Unity Tether → Status**.

**Python side** — install the SDK and CLI:

```bash
pip install -e .
tether ping
tether state
```
