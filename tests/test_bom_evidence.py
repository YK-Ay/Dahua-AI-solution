from app.builder import Bom


def test_bom_roundtrip_keeps_calculation_basis():
    bom = Bom(devices=[], storage={"raw_tb": 10}, bandwidth=120,
              lanes_total=20, gaps=[], assumptions=[],
              calculation_basis=["带宽：各场景路数 × 码流之和 × 1.2 = 120 Mbps"])
    restored = Bom.from_dict(bom.to_dict())
    assert restored.calculation_basis[0].endswith("120 Mbps")
