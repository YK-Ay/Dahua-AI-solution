from app.models import Device


def test_device_keeps_selection_evidence_chain():
    d = Device(category="网络摄像机", model="DH-TEST", qty=10,
               evidence=["园区综合安防方案"],
               alternatives=["DH-ALT-1"],
               selection_engine={"hits": 3, "shape_relaxed": False})
    data = d.model_dump()
    assert data["evidence"] == ["园区综合安防方案"]
    assert data["alternatives"] == ["DH-ALT-1"]
    assert data["selection_engine"]["hits"] == 3
