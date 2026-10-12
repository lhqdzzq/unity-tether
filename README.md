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

Early design phase — no code yet.

## Roadmap

- **P0** — minimal bridge (TCP + main-thread dispatch + `ping` / `state`)
- **P1** — debug loop (log forwarding, `exec` C#, screenshot)
- **P2** — control (scene / prefab / asset / script tools)
- **P3** — MCP server (tool set + screenshot feedback loop)
- **P4** — engineering (test-runner loop, multi-instance routing, Roslyn validation)

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
