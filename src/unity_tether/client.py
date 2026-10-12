"""TCP client for the Unity Tether bridge.

The bridge speaks an async, id-correlated JSON protocol. This client is a plain
synchronous front-end: it is allowed to block, because it lives in a separate
process and blocking here can never deadlock the Unity main thread.
"""

from __future__ import annotations

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
