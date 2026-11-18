import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.requirement_review import assess  # noqa: E402


def test_complete_requirement_is_ready():
    result = assess("智慧园区周界80路、出入口20路，录像保存30天")
    assert result["ready"] is True
    assert result["summary"] == {"confirmed": 5, "confirm": 0, "risk": 0}


def test_missing_hard_inputs_are_confirm_items():
    result = assess("某园区需要视频监控")
    assert result["ready"] is False
    assert {x["key"] for x in result["items"] if x["status"] == "confirm"} >= {
        "lanes", "retention_days"
    }


def test_form_values_override_missing_text():
    result = assess("校园周界监控", lanes=50, retention_days=45)
    assert result["detected"]["lanes"] is None
    assert all(x["status"] != "confirm" for x in result["items"] if x["key"] in {"lanes", "retention_days"})
