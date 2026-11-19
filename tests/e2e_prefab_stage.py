"""E2E test: Prefab Stage editing + screenshot source against a live Unity Editor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    # 1. create a source cube and save it as a prefab
    src = client.create_object(name="StageSource", primitive="Cube")
    saved = client.save_prefab(src["id"], "Assets/StageTest.prefab")
    print("saved:", saved)
    assert saved["success"] is True

    # 2. open it in the Prefab Stage (the official editing context)
    opened = client.open_prefab("Assets/StageTest.prefab")
    print("opened:", opened["path"], "root =", opened["prefabRoot"]["name"])

    # 3. get_scene should report the stage
    scene = client.get_scene()
    print("inPrefabStage =", scene["inPrefabStage"], "prefabPath =", scene["prefabPath"])
    assert scene["inPrefabStage"] is True

    # 4. create a child inside the stage (parented under the prefab root)
    child = client.create_object(name="StageChild", position=[0, 1, 0], parent=opened["prefabRoot"]["id"])
    print("child:", child)

    # 5. find it (search should hit the stage scene)
    found = client.find_objects(name="StageChild")
    assert len(found["objects"]) == 1, found
    print("found in stage:", found["objects"][0]["path"])

    # 6. close the stage
    closed = client.close_prefab()
    print("closed:", closed)
    assert closed["closed"] is True

    # 7. the prefab asset should now contain the child
    loaded = client.load_prefab("Assets/StageTest.prefab")
    children = client.find_objects(name="StageChild", root=loaded["root"]["id"])
    assert len(children["objects"]) == 1, children
    print("child persisted in prefab asset:", children["objects"][0]["path"])

    # 8. screenshot still works (camera source in edit mode)
    meta, png = client.screenshot(width=160, height=120, source="camera")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    print("camera screenshot OK:", meta["width"], "x", meta["height"])

    print("PASS: Prefab Stage editing + screenshot source verified")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
