from app.auto_fix import suggestions


def test_architecture_block_returns_manual_suggestions():
    result = suggestions({"items": [{"level": "block", "code": "disk_slots"}]})
    assert result["applied"] is False
    assert result["safe"] is False
    assert "盘位" in result["suggestions"][0]
