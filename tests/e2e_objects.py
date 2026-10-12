"""E2E test: scene/object control against a live Unity Editor (requires the bridge running)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    # 1. create a cube at (0, 1, 0) with a Rigidbody
    created = client.create_object(name="TestCube", primitive="Cube", components=["Rigidbody"], position=[0, 1, 0])
    print("created:", created)
    cube_id = created["id"]
    assert created["name"] == "TestCube"
    assert created["path"] == "/TestCube"
    assert "UnityEngine.Rigidbody" in created["components"], created["components"]

    # 2. find it
    found = client.find_objects("TestCube")
    assert len(found["objects"]) == 1, found
    print("found:", found)

    # 3. modify position + active
    modified = client.modify_object(cube_id, position=[2, 3, 4], active=False)
    print("modified:", modified)
    assert modified["position"] == [2.0, 3.0, 4.0], modified["position"]
    assert modified["active"] is False

    # 4. delete it
    deleted = client.delete_object(cube_id)
    assert deleted["deleted"] is True
    print("deleted:", deleted["object"]["name"])

    # 5. confirm it is gone
    gone = client.find_objects("TestCube")
    assert len(gone["objects"]) == 0, gone

    # 6. scene summary
    scene = client.get_scene()
    print("scene:", scene["name"], "rootCount =", scene["rootCount"])

    print("PASS: object CRUD + id addressing verified")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
