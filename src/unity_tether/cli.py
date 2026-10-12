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
        else:
            parser.error(f"unknown command: {args.command}")
        return 0
    except Exception as exc:  # TetherError and friends
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
