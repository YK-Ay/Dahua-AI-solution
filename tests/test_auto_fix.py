from app.auto_fix import suggestions
from app.auto_fix import storage_patch


def test_architecture_block_returns_manual_suggestions():
    result = suggestions({"items": [{"level": "block", "code": "disk_slots"}]})
    assert result["applied"] is False
    assert result["safe"] is False
    assert "盘位" in result["suggestions"][0]


def test_storage_patch_increases_nvr_for_disk_slots():
    patch = storage_patch({"devices": [
        {"category": "NVR", "model": "DH-NVR4432-HDS2/I", "qty": 6,
         "display_name": "网络硬盘录像机"},
        {"category": "机械硬盘", "model": "ST8000VM006", "qty": 30},
    ]})
    assert patch["qty"] == 15
    assert patch["slots"] == 2
