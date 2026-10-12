"""E2E test: asset management against a live Unity Editor (requires the bridge running)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    # 1. create a folder
    folder = client.create_folder("Assets/TetherTest")
    print("folder:", folder)
    assert folder["created"] is True

    # 2. create a material inside it
    mat = client.create_asset("Assets/TetherTest/TestMat.mat", "material")
    print("material:", mat)
    assert mat["created"] is True

    # 3. list assets — should contain the material
    listed = client.list_assets("Assets/TetherTest")
    print("listed:", listed)
    assert "Assets/TetherTest/TestMat.mat" in listed["assets"], listed

    # 4. move (rename) the material
    moved = client.move_asset("Assets/TetherTest/TestMat.mat", "Assets/TetherTest/Renamed.mat")
    print("moved:", moved)
    assert moved["moved"] is True

    # 5. list again — renamed, old name gone
    listed2 = client.list_assets("Assets/TetherTest")
    assert "Assets/TetherTest/Renamed.mat" in listed2["assets"], listed2
    assert "Assets/TetherTest/TestMat.mat" not in listed2["assets"], listed2

    # 6. delete it
    deleted = client.delete_asset("Assets/TetherTest/Renamed.mat")
    print("deleted:", deleted)
    assert deleted["deleted"] is True

    # 7. list — empty again
    listed3 = client.list_assets("Assets/TetherTest")
    assert listed3["assets"] == [], listed3

    print("PASS: asset CRUD verified")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
