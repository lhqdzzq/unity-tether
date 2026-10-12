"""E2E test: reflection 'call' against a live Unity Editor (requires the bridge running)."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    # 1. Return-value path (pure .NET, deterministic).
    result = client.invoke("System.Math", "Sqrt", [16])
    print("Math.Sqrt(16) ->", result)
    assert result["returnValue"] == "4", result

    # 2. Side-effect path: invoke Debug.Log during play mode, observe it via the push channel.
    collected: list[dict] = []
    stop_collecting = threading.Event()

    def collect():
        while not stop_collecting.is_set():
            try:
                collected.append(client.next_event(timeout=0.5))
            except Exception:
                pass

    thread = threading.Thread(target=collect, daemon=True)
    thread.start()

    try:
        client.play()
        time.sleep(3)
        log_result = client.invoke("UnityEngine.Debug", "Log", ["hello from call"])
        print("Debug.Log ->", log_result)
        time.sleep(1)
        client.stop()
        time.sleep(1)
    finally:
        stop_collecting.set()
        client.close()

    messages = [e.get("message", "") for e in collected if e.get("event") == "log"]
    print(f"collected {len(messages)} log messages")
    assert any("hello from call" in m for m in messages), f"no 'hello from call' in logs: {messages}"
    print("PASS: reflection call returned value and its side effect reached the log stream")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
