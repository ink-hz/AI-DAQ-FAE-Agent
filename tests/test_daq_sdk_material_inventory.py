"""Archived SDK assets remain inventory evidence, never device support."""

def test_material_inventory_deduplicates_bytes_and_keeps_unknown_pairing():
    try:
        from daq_fae.knowledge.sdk_materials import build_inventory, search_inventory
    except ModuleNotFoundError:
        assert False, "SDK material inventory builder is missing"
    rows = [
        {"path": "SDK/SDK&OrbbecViewer_v2.9.0_20260929/windows/EgoLowBle-1.1.8.zip",
         "sha256": "a" * 64, "size": 100},
        {"path": "SDK/SDK&OrbbecViewer_v2.9.0_20260923/windows/EgoLowBle-1.1.8.zip",
         "sha256": "a" * 64, "size": 100},
        {"path": "Tool/ios 安装包.url", "sha256": "b" * 64, "size": 20},
    ]
    inventory = build_inventory(rows, source_checked_packages={rows[0]["path"]})
    assert len(inventory) == 3
    assert inventory[0]["use_scope"] == "api_member_only"
    assert inventory[0]["platform_hint"] == "windows"
    assert inventory[0]["drop_hint"] == "20260929"
    assert inventory[1]["duplicate_of"] == inventory[0]["path"]
    assert inventory[1]["use_scope"] == "asset_identity_only"
    assert inventory[2]["use_scope"] == "unverified_pointer"
    assert all(row["device_support"] == "unknown" and
               row["download_authority"] == "unverified" for row in inventory)
    assert [row["path"] for row in search_inventory(inventory, "EgoLowBle")] == [
        rows[0]["path"], rows[1]["path"]]
