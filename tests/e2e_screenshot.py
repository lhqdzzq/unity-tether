"""E2E test: screenshot against a live Unity Editor (requires the bridge running)."""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402

WIDTH, HEIGHT = 320, 240


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    # Put a cube at the origin so the scene has something to render (PrimitiveType.Cube == 3).
    created = client.invoke("GameObject", "CreatePrimitive", [3])
    print("CreatePrimitive ->", created)

    meta, png = client.screenshot(width=WIDTH, height=HEIGHT)
    print("meta ->", {k: meta[k] for k in ("format", "width", "height")}, "bytes =", len(png))

    # Verify the PNG signature and its IHDR width/height.
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "response is not a PNG"
    png_width, png_height = struct.unpack(">II", png[16:24])
    assert (png_width, png_height) == (WIDTH, HEIGHT), (png_width, png_height)

    out = Path(__file__).resolve().parent.parent.parent / "unity-tether-sandbox" / "screenshot.png"
    out.write_bytes(png)
    print(f"PASS: valid PNG {png_width}x{png_height} ({len(png)} bytes) -> {out}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
