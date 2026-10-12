"""TCP client for the Unity Tether bridge.

The bridge speaks an async, id-correlated JSON protocol. This client is a plain
synchronous front-end: it is allowed to block, because it lives in a separate
process and blocking here can never deadlock the Unity main thread.
"""

from __future__ import annotations

import base64
import json
import queue
import socket
import threading
from collections.abc import Iterator

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class TetherError(Exception):
    """Base error for bridge failures."""


class UnityTetherClient:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, dict] = {}
        self._events: queue.Queue[dict] = queue.Queue()
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> "UnityTetherClient":
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        reader = threading.Thread(target=self._read_loop, daemon=True, name="tether-reader")
        reader.start()
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except Exception:
            pass

    def __enter__(self) -> "UnityTetherClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- protocol ----------------------------------------------------------
    def _read_loop(self) -> None:
        stream = self._sock.makefile("r", encoding="utf-8")
        for raw in stream:
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue

            message_id = message.get("id")
            if message_id is not None:
                slot = self._pending.pop(message_id, None)
                if slot is not None:
                    slot["response"] = message
                    slot["event"].set()
            else:
                self._events.put(message)

    def call(self, method: str, params: dict | None = None, timeout: float | None = None) -> object:
        if self._sock is None:
            raise TetherError("not connected; call connect() first")

        with self._lock:
            request_id = self._next_id
            self._next_id += 1

        done = threading.Event()
        slot = {"event": done, "response": None}
        self._pending[request_id] = slot

        payload = json.dumps({"id": request_id, "method": method, "params": params or {}})
        self._sock.sendall((payload + "\n").encode("utf-8"))

        if not done.wait(timeout if timeout is not None else self.timeout):
            self._pending.pop(request_id, None)
            raise TetherError(f"timed out waiting for '{method}' (id={request_id})")

        response = slot["response"]
        if not response.get("ok"):
            raise TetherError(f"'{method}' failed: {response.get('error', 'unknown error')}")
        return response.get("result")

    # -- events ------------------------------------------------------------
    def next_event(self, timeout: float | None = None) -> dict:
        """Return the next push event (log etc.), blocking up to *timeout* seconds."""
        return self._events.get(timeout=timeout)

    def events(self) -> Iterator[dict]:
        """Yield push events as they arrive (infinite generator)."""
        while True:
            yield self.next_event()

    # -- convenience -------------------------------------------------------
    def ping(self) -> object:
        return self.call("ping")

    def state(self) -> object:
        return self.call("state")

    def play(self) -> object:
        return self.call("play")

    def stop(self) -> object:
        return self.call("stop")

    def invoke(self, type_name: str, method_name: str, args: list | None = None) -> object:
        """Invoke a public static method by reflection (e.g. invoke('UnityEngine.Debug', 'Log', ['hi']))."""
        return self.call("call", {"type": type_name, "method": method_name, "args": args or []})

    def screenshot(self, width: int = 800, height: int = 600) -> tuple[object, bytes]:
        """Capture a screenshot; returns (metadata, png_bytes)."""
        result = self.call("screenshot", {"width": width, "height": height})
        png = base64.b64decode(result["base64"])
        return result, png

    # -- scene / object control --------------------------------------------
    def create_object(self, name="GameObject", primitive=None, components=None, position=None, rotation=None, scale=None, parent=None) -> object:
        params: dict = {"name": name}
        if primitive is not None:
            params["primitive"] = primitive
        if components:
            params["components"] = components
        if position is not None:
            params["position"] = position
        if rotation is not None:
            params["rotation"] = rotation
        if scale is not None:
            params["scale"] = scale
        if parent is not None:
            params["parent"] = parent
        return self.call("create_object", params)

    def find_objects(self, name: str | None = None, root: object | None = None) -> object:
        params: dict = {}
        if name is not None:
            params["name"] = name
        if root is not None:
            params["root"] = root
        return self.call("find_objects", params)

    def modify_object(self, obj, **changes) -> object:
        return self.call("modify_object", {"object": obj, **changes})

    def delete_object(self, obj) -> object:
        return self.call("delete_object", {"object": obj})

    def get_scene(self) -> object:
        return self.call("get_scene", {})

    # -- prefab control ----------------------------------------------------
    def load_prefab(self, path: str) -> object:
        return self.call("load_prefab", {"path": path})

    def save_prefab(self, obj, path: str) -> object:
        return self.call("save_prefab", {"object": obj, "path": path})

    def instantiate_prefab(self, path: str) -> object:
        return self.call("instantiate_prefab", {"path": path})
