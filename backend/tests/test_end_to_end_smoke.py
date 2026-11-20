"""端到端冒烟：用临时库通过完整 ASGI 栈驱动应用（TestClient）。

核对题述主链路：
封水表后电表仍可写实抄；缺类型被拒；水/电总览计数与写闸一致；
类型批列表同口径；已封类型批不能改类型撬动。
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
import app.config as config
import app.db as db
from app.seed import seed_if_empty

PERIOD = "2026-10"


@pytest.fixture
def http_client(tmp_path, monkeypatch):
    db_file = tmp_path / "meterseal.db"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db_file)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", db_file)
    seed_if_empty()
    with TestClient(app, base_url="http://t/api") as c:
        yield c


def test_end_to_end_typed_seal_flow(http_client):
    c = http_client

    # 缺类型参数：对齐与封印都被拒
    assert c.post("/align", json={"period_id": PERIOD}).status_code == 422
    assert c.post("/seal", json={"period_id": PERIOD}).status_code == 422
    assert c.post("/seal", json={"period_id": PERIOD, "meter_type": "gas"}).status_code == 400

    # 分类型对齐
    rw = c.post("/align", json={"period_id": PERIOD, "meter_type": "water"})
    assert rw.status_code == 200
    re_ = c.post("/align", json={"period_id": PERIOD, "meter_type": "electric"})
    assert re_.status_code == 200

    # 类型过滤：批/差额不混桶
    wb = c.get(f"/batches?period_id={PERIOD}&meter_type=water").json()
    eb = c.get(f"/batches?period_id={PERIOD}&meter_type=electric").json()
    assert all(b["meter_type"] == "water" for b in wb)
    assert all(b["meter_type"] == "electric" for b in eb)

    # 封水表
    seal = c.post("/seal", json={
        "period_id": PERIOD, "meter_type": "water",
        "batch_id": rw.json()["batch_id"],
    })
    assert seal.status_code == 200
    assert seal.json()["meter_type"] == "water"

    # 水表拒写、电表仍可写实抄
    blocked = c.post("/actuals", json={
        "meter_id": "M-W-101", "period_id": PERIOD, "qty": 1,
        "occurred_at": "2026-10-29T10:00:00", "doc_no": "E2E-W",
    })
    assert blocked.status_code == 409
    allowed = c.post("/actuals", json={
        "meter_id": "M-E-101", "period_id": PERIOD, "qty": 222,
        "occurred_at": "2026-10-29T10:00:00", "doc_no": "E2E-E",
    })
    assert allowed.status_code == 200

    # 已封的水表批不能换电表类型撬动
    cross = c.post("/seal", json={
        "period_id": PERIOD, "meter_type": "electric",
        "batch_id": rw.json()["batch_id"],
    })
    assert cross.status_code in (400, 409)

    # 总览计数与写闸一致
    ov = c.get("/overview").json()
    assert ov["gate_counts"]["water"] == {"open": 1, "sealed": 1}
    assert ov["gate_counts"]["electric"] == {"open": 2, "sealed": 0}
    gates = {g["meter_type"]: g["status"] for g in ov["current_gates"]}
    assert gates == {"water": "sealed", "electric": "open"}

    # /periods 同口径
    ps = {p["period_id"]: p for p in c.get("/periods").json()}
    assert ps[PERIOD]["sealed_types"] == ["water"]

    # 再封电表后两类均拒
    seal_e = c.post("/seal", json={"period_id": PERIOD, "meter_type": "electric"})
    assert seal_e.status_code == 200
    assert c.post("/actuals", json={
        "meter_id": "M-E-101", "period_id": PERIOD, "qty": 1,
        "occurred_at": "2026-10-29T10:01:00", "doc_no": "E2E-E2",
    }).status_code == 409

    # 读接口类型过滤生效
    w_actuals = c.get(f"/actuals?period_id={PERIOD}&meter_type=water").json()
    assert all(r["meter_type"] == "water" for r in w_actuals)
    e_actuals = c.get(f"/actuals?period_id={PERIOD}&meter_type=electric").json()
    assert all(r["meter_type"] == "electric" for r in e_actuals)
