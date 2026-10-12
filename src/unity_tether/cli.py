"""Command-line interface for Unity Tether (debug-oriented front-end)."""

from __future__ import annotations

import argparse
import json
import sys

from .client import DEFAULT_HOST, DEFAULT_PORT, UnityTetherClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tether",
        description="Control and inspect the Unity Editor over the Unity Tether bridge.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bridge host (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bridge port (default {DEFAULT_PORT})")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ping", help="check that the bridge is reachable")
    sub.add_parser("state", help="print the editor state")
    sub.add_parser("play", help="enter play mode")
    sub.add_parser("stop", help="exit play mode")
    logs_parser = sub.add_parser("logs", help="stream runtime logs")
    logs_parser.add_argument(
        "--level",
        choices=["Log", "Warning", "Error", "Assert", "Exception"],
        help="filter by log level",
    )
    call_parser = sub.add_parser("call", help="invoke a static method (e.g. 'tether call UnityEngine.Debug Log hello')")
    call_parser.add_argument("type", help="type name, e.g. UnityEngine.Debug or System.Math")
    call_parser.add_argument("method", help="static method name")
    call_parser.add_argument("args", nargs="*", help="arguments as JSON values (5, true, \"text\", {\"x\":1,\"y\":2,\"z\":3})")
    shot_parser = sub.add_parser("screenshot", help="capture a screenshot of the current view")
    shot_parser.add_argument("--out", "-o", default="screenshot.png", help="output PNG path (default screenshot.png)")
    shot_parser.add_argument("--width", type=int, default=800)
    shot_parser.add_argument("--height", type=int, default=600)

    sub.add_parser("scene", help="show the active scene summary")

    find_parser = sub.add_parser("find-objects", help="find GameObjects by name")
    find_parser.add_argument("name")

    create_parser = sub.add_parser("create-object", help="create a GameObject")
    create_parser.add_argument("--name", default="GameObject")
    create_parser.add_argument("--primitive", choices=["Sphere", "Capsule", "Cylinder", "Cube", "Plane", "Quad"])
    create_parser.add_argument("--position", default=None, help="e.g. '0,1,0'")
    create_parser.add_argument("--component", action="append", default=None, help="component type to add (repeatable)")

    delete_parser = sub.add_parser("delete-object", help="delete a GameObject by id, path, or name")
    delete_parser.add_argument("ref")

    set_parser = sub.add_parser("set", help="modify a GameObject (position/active)")
    set_parser.add_argument("ref")
    set_parser.add_argument("--position", default=None, help="e.g. '0,1,0'")
    set_parser.add_argument("--active", choices=["true", "false"], default=None)

    load_parser = sub.add_parser("load-prefab", help="load prefab contents for editing")
    load_parser.add_argument("path")

    save_parser = sub.add_parser("save-prefab", help="save an object as a prefab asset")
    save_parser.add_argument("ref")
    save_parser.add_argument("path")

    inst_parser = sub.add_parser("instantiate-prefab", help="instantiate a prefab into the scene")
    inst_parser.add_argument("path")

    args = parser.parse_args(argv)

    client = UnityTetherClient(args.host, args.port)
    try:
        client.connect()
    except OSError as exc:
        print(f"error: cannot connect to {args.host}:{args.port} — {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "ping":
            print(json.dumps(client.ping(), indent=2))
        elif args.command == "state":
            print(json.dumps(client.state(), indent=2))
        elif args.command == "play":
            print(json.dumps(client.play(), indent=2))
        elif args.command == "stop":
            print(json.dumps(client.stop(), indent=2))
        elif args.command == "logs":
            return _stream_logs(client, args.level)
        elif args.command == "call":
            parsed = [_parse_json_arg(a) for a in args.args]
            print(json.dumps(client.invoke(args.type, args.method, parsed), indent=2))
        elif args.command == "screenshot":
            return _save_screenshot(client, args)
        elif args.command == "scene":
            print(json.dumps(client.get_scene(), indent=2))
        elif args.command == "find-objects":
            print(json.dumps(client.find_objects(args.name), indent=2))
        elif args.command == "create-object":
            result = client.create_object(
                name=args.name,
                primitive=args.primitive,
                components=args.component,
                position=_parse_vec3(args.position),
            )
            print(json.dumps(result, indent=2))
        elif args.command == "delete-object":
            print(json.dumps(client.delete_object(_parse_ref(args.ref)), indent=2))
        elif args.command == "set":
            changes = {}
            if args.position is not None:
                changes["position"] = _parse_vec3(args.position)
            if args.active is not None:
                changes["active"] = args.active == "true"
            print(json.dumps(client.modify_object(_parse_ref(args.ref), **changes), indent=2))
        elif args.command == "load-prefab":
            print(json.dumps(client.load_prefab(args.path), indent=2))
        elif args.command == "save-prefab":
            print(json.dumps(client.save_prefab(_parse_ref(args.ref), args.path), indent=2))
        elif args.command == "instantiate-prefab":
            print(json.dumps(client.instantiate_prefab(args.path), indent=2))
        else:
            parser.error(f"unknown command: {args.command}")
        return 0
    except Exception as exc:  # TetherError and friends
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


def _parse_json_arg(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_vec3(raw: str | None) -> list[float] | None:
    if raw is None:
        return None
    return [float(x) for x in raw.split(",")]


def _parse_ref(raw: str) -> object:
    try:
        return int(raw)
    except ValueError:
        return {"path": raw} if raw.startswith("/") else {"name": raw}


def _save_screenshot(client: UnityTetherClient, args) -> int:
    meta, png = client.screenshot(width=args.width, height=args.height)
    with open(args.out, "wb") as f:
        f.write(png)
    print(
        json.dumps(
            {
                "format": meta.get("format"),
                "width": meta.get("width"),
                "height": meta.get("height"),
                "bytes": len(png),
                "path": args.out,
            },
            indent=2,
        )
    )
    return 0


def _stream_logs(client: UnityTetherClient, level: str | None) -> int:
    print("streaming logs (Ctrl+C to stop)…", file=sys.stderr)
    try:
        for event in client.events():
            if event.get("event") != "log":
                continue
            if level and event.get("type") != level:
                continue
            print(f"[{event.get('type', '?')}] {event.get('message', '')}", flush=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
