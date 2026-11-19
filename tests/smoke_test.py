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
            elif method == "create_object":
                result = {"id": 42, "name": message["params"]["name"], "path": "/" + message["params"]["name"], "active": True, "position": [0.0, 0.0, 0.0], "components": []}
            elif method == "find_objects":
                result = {"objects": []}
            elif method == "modify_object":
                result = {"id": 1, "name": "x", "path": "/x", "active": True, "position": [0.0, 0.0, 0.0], "components": []}
            elif method == "delete_object":
                result = {"deleted": True, "object": {"id": 1}}
            elif method == "get_scene":
                result = {"name": "Mock", "path": "", "rootCount": 0, "roots": []}
            elif method == "load_prefab":
                result = {"path": message["params"]["path"], "root": {"id": 100, "name": "PrefabRoot", "path": "/PrefabRoot", "active": True, "position": [0.0, 0.0, 0.0], "components": []}}
            elif method == "save_prefab":
                result = {"path": message["params"]["path"], "success": True}
            elif method == "instantiate_prefab":
                result = {"id": 101, "name": "PrefabRoot(Clone)", "path": "/PrefabRoot(Clone)", "active": True, "position": [0.0, 0.0, 0.0], "components": []}
            elif method == "open_prefab":
                result = {"path": message["params"]["path"], "prefabRoot": {"id": 200, "name": "X", "path": "/X", "active": True, "position": [0.0, 0.0, 0.0], "components": []}, "roots": []}
            elif method == "close_prefab":
                result = {"closed": True}
            elif method == "create_folder":
                result = {"path": message["params"]["path"], "created": True}
            elif method == "create_asset":
                result = {"path": message["params"]["path"], "type": message["params"]["type"], "created": True}
            elif method == "move_asset":
                result = {"src": message["params"]["src"], "dst": message["params"]["dst"], "moved": True}
            elif method == "delete_asset":
                result = {"path": message["params"]["path"], "deleted": True}
            elif method == "list_assets":
                result = {"folder": message["params"]["folder"], "assets": []}
            elif method == "run_tests":
                result = {"started": True, "mode": "edit"}
                conn.sendall(
                    (json.dumps({"event": "test_results", "resultState": "Failed", "passed": 1, "failed": 1, "skipped": 0}) + "\n").encode("utf-8")
                )
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
            assert client.get_scene()["name"] == "Mock"
            assert client.create_object(name="X")["id"] == 42
            assert client.find_objects("X")["objects"] == []
            assert client.modify_object(42, position=[0, 0, 0])["id"] == 1
            assert client.delete_object(42)["deleted"] is True
            assert client.load_prefab("Assets/X.prefab")["root"]["name"] == "PrefabRoot"
            assert client.save_prefab(100, "Assets/X.prefab")["success"] is True
            assert client.instantiate_prefab("Assets/X.prefab")["name"] == "PrefabRoot(Clone)"
            assert client.open_prefab("Assets/X.prefab")["path"] == "Assets/X.prefab"
            assert client.close_prefab()["closed"] is True
            assert client.create_folder("Assets/X")["created"] is True
            assert client.create_asset("Assets/X/M.mat", "material")["created"] is True
            assert client.move_asset("Assets/X/M.mat", "Assets/X/N.mat")["moved"] is True
            assert client.delete_asset("Assets/X/N.mat")["deleted"] is True
            assert client.list_assets("Assets/X")["assets"] == []
            test_result = client.run_tests(mode="edit")
            assert test_result["event"] == "test_results" and test_result["passed"] == 1 and test_result["failed"] == 1, test_result
            print("run_tests ->", test_result)
            assert client.stop()["state"] == "exiting-playmode"
        print("PASS")
        return 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
