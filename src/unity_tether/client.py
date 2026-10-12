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
import time
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
        self._events: queue.Queue = queue.Queue()  # holds dict events or None (disconnect sentinel)
        self._reader_stopped = False
        self._generation = 0
        self._reconnect_lock = threading.Lock()
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> "UnityTetherClient":
        self._open()
        return self

    def _open(self) -> None:
        self._closed = False
        self._reader_stopped = False
        self._generation += 1
        gen = self._generation
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        reader = threading.Thread(target=self._read_loop, args=(gen,), daemon=True, name="tether-reader")
        reader.start()

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

    def _reconnect(self) -> None:
        """Reconnect after the bridge dropped the link (Domain Reload / restart)."""
        with self._reconnect_lock:
            if self._sock is not None and not self._reader_stopped:
                return  # already connected
            try:
                if self._sock is not None:
                    self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._open()

    # -- protocol ----------------------------------------------------------
    def _read_loop(self, gen: int) -> None:
        try:
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
        finally:
            # Only the current reader may mark the connection as broken; a stale
            # reader (from a previous generation) must not touch the new one.
            if gen == self._generation:
                self._reader_stopped = True
                if not self._closed:
                    self._events.put(None)  # sentinel: unexpected disconnect

    def call(self, method: str, params: dict | None = None, timeout: float | None = None) -> object:
        if self._sock is None:
            raise TetherError("not connected; call connect() first")

        for attempt in range(2):
            try:
                if self._reader_stopped:
                    self._reconnect()
                return self._call_once(method, params, timeout)
            except (OSError, TetherError):
                if attempt == 0:
                    self._reader_stopped = True  # force reconnect on retry
                    continue
                raise

    def _call_once(self, method: str, params: dict | None, timeout: float | None) -> object:
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
        while True:
            event = self._events.get(timeout=timeout)
            if event is None:
                # Connection lost — reconnect and keep waiting.
                self._reconnect()
                continue
            return event

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

    # -- asset control -----------------------------------------------------
    def create_folder(self, path: str) -> object:
        return self.call("create_folder", {"path": path})

    def create_asset(self, path: str, type_: str = "material") -> object:
        return self.call("create_asset", {"path": path, "type": type_})

    def move_asset(self, src: str, dst: str) -> object:
        return self.call("move_asset", {"src": src, "dst": dst})

    def delete_asset(self, path: str) -> object:
        return self.call("delete_asset", {"path": path})

    def list_assets(self, folder: str = "Assets", recursive: bool = False) -> object:
        return self.call("list_assets", {"folder": folder, "recursive": recursive})

    # -- test runner --------------------------------------------------------
    def run_tests(self, mode: str = "edit", timeout: float | None = None) -> object:
        """Run Unity tests and wait for the asynchronous test_results event."""
        self.call("run_tests", {"mode": mode})
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TetherError("timed out waiting for test results")
            try:
                event = self.next_event(timeout=remaining)
            except queue.Empty:
                raise TetherError("timed out waiting for test results")
            if event.get("event") == "test_results":
                return event
