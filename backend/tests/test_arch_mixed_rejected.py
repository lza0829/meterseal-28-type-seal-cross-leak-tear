"""架构 B（混类型整账期一把封）反向核对。

本系统全局只贯彻架构 A（按 meter_type 分桶）。本组用例证明架构 B 的
契约在本实现中不成立，防止回归成混桶封印或楼栋分区封印换皮：
- 无类型的封印/对齐请求必须被拒（不存在"未带类型却宣称分类型封"）；
- 封一类后另一类仍可写（不是整账期一并拒写）；
- 一个批不可能同时含水表与电表（不是一批含全部表）；
- 分桶键是 meter_type 而非 building（不是楼栋分区换皮）。
"""
from fastapi.testclient import TestClient

PERIOD = "2026-10"


def test_legacy_typeless_seal_is_rejected_not_period_wide(client: TestClient):
    # 先把两类都对齐，制造"可以整期封"的假象
    client.post("/api/align", json={"period_id": PERIOD, "meter_type": "water"})
    client.post("/api/align", json={"period_id": PERIOD, "meter_type": "electric"})

    # 架构 B 的入口是无类型 /seal；本实现必须拒绝，而非整期一把封
    r = client.post("/api/seal", json={"period_id": PERIOD})
    assert r.status_code == 422

    # 两条写闸都仍开放：没有任何类型被顺手封掉
    ov = client.get("/api/overview").json()
    assert ov["gate_counts"]["water"]["sealed"] == 0
    assert ov["gate_counts"]["electric"]["sealed"] == 0


def test_mixed_seal_does_not_reject_both_types(client: TestClient):
    """架构 B 封后两类均拒写；架构 A 必须只拒被封的那一类。"""
    client.post("/api/align", json={"period_id": PERIOD, "meter_type": "water"})
    seal = client.post(
        "/api/seal", json={"period_id": PERIOD, "meter_type": "water"}
    )
    assert seal.status_code == 200

    water_write = client.post(
        "/api/actuals",
        json={
            "meter_id": "M-W-101", "period_id": PERIOD, "qty": 1,
            "occurred_at": "2026-10-29T10:00:00", "doc_no": "MIX-W",
        },
    )
    elec_write = client.post(
        "/api/actuals",
        json={
            "meter_id": "M-E-101", "period_id": PERIOD, "qty": 1,
            "occurred_at": "2026-10-29T10:00:00", "doc_no": "MIX-E",
        },
    )
    assert water_write.status_code == 409
    # 若这里是 409，说明退化成了混类型整账期一把封
    assert elec_write.status_code == 200


def test_no_batch_ever_contains_both_meter_types(client: TestClient):
    client.post("/api/align", json={"period_id": PERIOD, "meter_type": "water"})
    client.post("/api/align", json={"period_id": PERIOD, "meter_type": "electric"})

    for batch in client.get(f"/api/batches?period_id={PERIOD}").json():
        types_in_batch = {
            v["meter_type"]
            for v in client.get(
                f"/api/variances?batch_id={batch['batch_id']}"
            ).json()
        }
        # 无差额的批 types_in_batch 为空集；有差额则只能是单类型
        assert len(types_in_batch) <= 1
        assert batch["meter_type"] in ("water", "electric")


def test_bucketing_is_meter_type_not_building(client: TestClient):
    """A栋同时有水表 M-W-101 和电表 M-E-101；分桶必须按 meter_type 拆开。"""
    water = client.post(
        "/api/align", json={"period_id": PERIOD, "meter_type": "water"}
    ).json()
    electric = client.post(
        "/api/align", json={"period_id": PERIOD, "meter_type": "electric"}
    ).json()

    wv = client.get(f"/api/variances?batch_id={water['batch_id']}").json()
    ev = client.get(f"/api/variances?batch_id={electric['batch_id']}").json()
    water_meters = {v["meter_id"] for v in wv}
    elec_meters = {v["meter_id"] for v in ev}

    # 同属 A栋的两类表落在不同类型批，证明不是按楼栋分区封印
    assert "M-W-101" not in elec_meters
    assert "M-E-101" not in water_meters
    assert water_meters.isdisjoint(elec_meters)

    # 类型过滤参数非法值不允许静默退化为全量混桶
    r = client.get(f"/api/batches?period_id={PERIOD}&meter_type=gas")
    assert r.json() == []
