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
            elif method == "play":
                result = {"state": "entering-playmode"}
                # push a log event to exercise the push channel
                conn.sendall(
                    (json.dumps({"event": "log", "type": "Log", "message": "hello from mock"}) + "\n").encode("utf-8")
                )
            elif method == "stop":
                result = {"state": "exiting-playmode"}
            elif method == "call":
                result = {"returnValue": "mock-return", "returnType": "System.String"}
            elif method == "screenshot":
                result = {"format": "png", "width": 2, "height": 2, "base64": "aGVsbG8="}
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
            assert client.ping()["pong"] is True
            assert client.state()["mode"] == "edit"
            assert client.play()["state"] == "entering-playmode"

            # The mock pushed a log event before the play response; the client
            # must route id-less messages to the event queue, not the pending map.
            event = client.next_event(timeout=2)
            assert event["event"] == "log" and event["message"] == "hello from mock", event
            print("event ->", json.dumps(event, ensure_ascii=False))

            assert client.invoke("System.Math", "Sqrt", [16])["returnValue"] == "mock-return"
            meta, png = client.screenshot(width=2, height=2)
            assert meta["format"] == "png" and png == b"hello", (meta, png)
            print("screenshot ->", meta, png)
            assert client.stop()["state"] == "exiting-playmode"
        print("PASS")
        return 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
