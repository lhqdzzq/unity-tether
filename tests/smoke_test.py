"""Smoke test: exercise UnityTetherClient against a mock bridge (no Unity needed)."""

import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402

PORT = 18765


def mock_bridge():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen(1)

    def run():
        conn, _ = server.accept()
        stream = conn.makefile("r", encoding="utf-8")
        for raw in stream:
            message = json.loads(raw)
            method = message.get("method")
            if method == "ping":
                result = {"pong": True, "time": 1234567890, "unityVersion": "mock"}
            elif method == "state":
                result = {"mode": "edit", "isPlaying": False, "sceneName": "MockScene"}
            else:
                result = None
            conn.sendall(
                (json.dumps({"id": message["id"], "ok": True, "result": result}) + "\n").encode("utf-8")
            )
        conn.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server, thread


def main() -> int:
    server, _ = mock_bridge()
    try:
        with UnityTetherClient(port=PORT) as client:
            pong = client.ping()
            assert pong["pong"] is True, pong
            print("ping ->", json.dumps(pong, ensure_ascii=False))

            state = client.state()
            assert state["mode"] == "edit", state
            print("state ->", json.dumps(state, ensure_ascii=False))
        print("PASS")
        return 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
