"""E2E test: log forwarding against a live Unity Editor (requires the bridge running)."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402


def main() -> int:
    client = UnityTetherClient()
    client.connect()

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
        print("entering play mode…")
        print("play ->", client.play())
        time.sleep(4)  # wait for LogEmitter to log on play-mode load
        print("stop ->", client.stop())
        time.sleep(1)
    finally:
        stop_collecting.set()
        client.close()

    logs = [e for e in collected if e.get("event") == "log"]
    print(f"collected {len(logs)} log events")
    for event in logs:
        print(f"  [{event.get('type')}] {event.get('message')}")

    assert logs, "no log events received"
    assert any("play mode entered" in e.get("message", "") for e in logs), "no play-mode-entered log"
    print("PASS: received runtime logs over the push channel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
