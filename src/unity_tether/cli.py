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
        else:
            parser.error(f"unknown command: {args.command}")
        return 0
    except Exception as exc:  # TetherError and friends
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


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
