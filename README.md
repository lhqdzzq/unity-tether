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
