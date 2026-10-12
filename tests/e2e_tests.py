"""E2E test: run Unity tests via the bridge and receive results (requires Unity running)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    result = client.run_tests(mode="edit")
    print("test_results ->", result)

    assert result["event"] == "test_results", result
    assert result["passed"] == 1, result
    assert result["failed"] == 1, result
    assert result["resultState"].startswith("Failed"), result

    print("PASS: test runner verified (1 passed, 1 failed)")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
