"""E2E test: client auto-reconnect after a dropped connection (requires Unity running)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    assert client.ping()["pong"] is True
    print("ping #1 OK")

    # Simulate the bridge dropping the link (as Unity does on restart / Domain Reload).
    client._sock.close()
    time.sleep(0.3)  # let the reader thread observe EOF and mark the link broken

    # The next call must transparently reconnect.
    assert client.ping()["pong"] is True
    print("ping #2 OK (auto-reconnected)")

    client.close()
    print("PASS: auto-reconnect verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
