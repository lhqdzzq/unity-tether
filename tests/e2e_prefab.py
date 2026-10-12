"""E2E test: prefab save/load/edit/instantiate against a live Unity Editor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from unity_tether import UnityTetherClient  # noqa: E402

PREFAB_PATH = "Assets/TestPrefab.prefab"


def main() -> int:
    client = UnityTetherClient()
    client.connect()

    # 1. create a source object in the scene
    src = client.create_object(name="PrefabSource", primitive="Cube", position=[1, 1, 1])
    print("source:", src)

    # 2. save it as a prefab asset
    saved = client.save_prefab(src["id"], PREFAB_PATH)
    print("saved:", saved)
    assert saved["success"] is True

    # 3. load the prefab contents for editing
    loaded = client.load_prefab(PREFAB_PATH)
    root_id = loaded["root"]["id"]
    print("loaded root:", loaded["root"])
    assert loaded["root"]["name"] == "TestPrefab"

    # 4. add a child inside the prefab contents
    child = client.create_object(name="Child", parent=root_id, position=[0, 1, 0])
    print("child:", child)

    # 5. save the modified contents back to the asset
    resaved = client.save_prefab(root_id, PREFAB_PATH)
    print("resaved:", resaved)
    assert resaved["success"] is True

    # 6. reload and confirm the child is persisted
    reloaded = client.load_prefab(PREFAB_PATH)
    children = client.find_objects(name="Child", root=reloaded["root"]["id"])
    print("children in reloaded prefab:", children)
    assert len(children["objects"]) == 1, children

    # 7. instantiate into the scene and confirm the child comes with it
    instance = client.instantiate_prefab(PREFAB_PATH)
    print("instance:", instance)
    assert instance["name"] == "TestPrefab"
    scene_children = client.find_objects(name="Child")
    assert len(scene_children["objects"]) >= 1, scene_children

    print("PASS: prefab save/load/edit/instantiate verified")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
