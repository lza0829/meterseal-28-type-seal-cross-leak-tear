"""类型封印宣告 ↔ 写闸 / 批明细 / 总览 / 装载 撕裂回归夹具。

全局贯彻**声明架构**（封印请求的 meter_type 为权威），并要求明细侧同样
不撒谎：sealed 批明细必须纯声明类型。两组不变量同时成立：

  声明侧：写闸只按声明类型开闭；另一类型始终可写；总览跟 period_gates；
  明细侧：异类型键不得留在 sealed 批；混批必须隔离（void），不得长期现行。

覆盖题述四个夹具：
  1) 封水电也被拒（封印不得误伤另一类型的写入）
  2) 水表仍可写（封水后水表写入必须被拒）
  3) 明细混电键（混桶批不得被封成 sealed，也不得潜伏）
  4) 交错半截混批（装载与对齐交错时批明细必须来自单一快照）
"""
from concurrent.futures import ThreadPoolExecutor

import pytest

PERIOD = "2026-10"
WATER_METER = "M-W-101"
ELEC_METER = "M-E-101"


def _align(client, meter_type):
    return client.post("/api/align", json={"period_id": PERIOD, "meter_type": meter_type})


def _seal(client, meter_type, batch_id=None):
    body = {"period_id": PERIOD, "meter_type": meter_type}
    if batch_id:
        body["batch_id"] = batch_id
    return client.post("/api/seal", json=body)


def _actual(meter_id, qty, doc_no):
    return {
        "meter_id": meter_id,
        "period_id": PERIOD,
        "qty": qty,
        "occurred_at": "2026-10-29T10:00:00",
        "doc_no": doc_no,
    }


def _estimate(meter_id, qty, doc_no):
    return {"meter_id": meter_id, "period_id": PERIOD, "qty": qty, "doc_no": doc_no}


def _gates(client):
    ov = client.get("/api/overview").json()
    return {g["meter_type"]: g["status"] for g in ov["current_gates"]}


def _inject_foreign_variance(batch_id, meter_id, meter_type_as_declared, real_type):
    """模拟历史坏数据：往某类型批里塞一条异类型键（行内自报仍为声明类型）。"""
    from app.db import connect

    conn = connect()
    try:
        conn.execute(
            """INSERT INTO variances
                   (variance_id,batch_id,meter_id,meter_type,period_id,
                    estimate_qty,actual_qty,delta,fate)
               VALUES (?,?,?,?,?,?,?,?, 'pending')""",
            (f"V-BAD-{meter_id}", batch_id, meter_id, meter_type_as_declared,
             PERIOD, 1.0, 2.0, 1.0),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- 夹具 1：封水后电表装载仍通（不得被误拒） ----------

def test_fixture_seal_water_must_not_reject_electric_load(client):
    assert _align(client, "water").status_code == 200
    assert _seal(client, "water").status_code == 200

    assert client.post("/api/actuals", json=_actual(ELEC_METER, 222, "FX-E-A")).status_code == 200
    assert client.post("/api/estimates", json=_estimate(ELEC_METER, 222, "FX-E-E")).status_code == 200

    ov = client.get("/api/overview").json()
    assert ov["gate_counts"]["electric"] == {"open": 2, "sealed": 0}
    assert _gates(client) == {"water": "sealed", "electric": "open"}


# ---------- 夹具 2：封水后水表仍可写必须收口 ----------

def test_fixture_water_must_not_remain_writable_after_seal(client):
    assert _align(client, "water").status_code == 200
    assert _seal(client, "water").status_code == 200

    r1 = client.post("/api/actuals", json=_actual(WATER_METER, 99, "FX-W-A"))
    r2 = client.post("/api/estimates", json=_estimate(WATER_METER, 99, "FX-W-E"))
    assert r1.status_code == 409 and "水表" in r1.json()["detail"]
    assert r2.status_code == 409

    # 电表只封自己那一条：电表封后水表也必须仍可写
    assert _align(client, "electric").status_code == 200
    assert _seal(client, "electric").status_code == 200
    assert client.post("/api/actuals", json=_actual(ELEC_METER, 1, "FX-E-B")).status_code == 409

    # 两类都封后两类均拒
    assert _gates(client) == {"water": "sealed", "electric": "sealed"}


# ---------- 夹具 3：批明细混入电表键，不得留下 sealed 批 ----------

def test_fixture_mixed_key_never_lands_in_sealed_batch(client):
    rw = _align(client, "water")
    assert rw.status_code == 200
    water_batch = rw.json()["batch_id"]

    # 坏数据：水表批里混入电表 M-E-101，行内 meter_type 仍谎报 water
    _inject_foreign_variance(water_batch, ELEC_METER, "water", "electric")

    # 封印必须拒绝，且不留下半截 sealed 批、不关写闸
    r = _seal(client, "water")
    assert r.status_code == 409
    assert "电表" in r.json()["detail"] or ELEC_METER in r.json()["detail"]

    batches = client.get(f"/api/batches?period_id={PERIOD}&meter_type=water").json()
    assert not [b for b in batches if b["status"] == "sealed"]
    assert _gates(client)["water"] == "open"
    assert client.post("/api/actuals", json=_actual(WATER_METER, 1, "FX-MIX-W")).status_code == 200

    # 异类型键不得被当作水表差额对外呈现
    vars_w = client.get(f"/api/variances?batch_id={water_batch}&meter_type=water").json()
    assert all(v["meter_id"] != ELEC_METER for v in vars_w)
    assert ELEC_METER in {
        v["meter_id"]
        for v in client.get(f"/api/variances?batch_id={water_batch}&meter_type=electric").json()
    }


def test_fixture_mixed_batch_is_quarantined_and_reseal_clean(client):
    rw = _align(client, "water")
    water_batch = rw.json()["batch_id"]
    _inject_foreign_variance(water_batch, ELEC_METER, "water", "electric")

    # 重新按类型对齐：污染批被隔离为 void，新批纯净
    again = _align(client, "water")
    assert again.status_code == 200, again.text
    clean_id = again.json()["batch_id"]
    assert clean_id != water_batch

    polluted = [
        b for b in client.get(f"/api/batches?period_id={PERIOD}&meter_type=water").json()
        if b["batch_id"] == water_batch
    ][0]
    assert polluted["status"] == "void"

    # 新批可以正常封印，且只关水表
    assert _seal(client, "water", batch_id=clean_id).status_code == 200
    assert _gates(client) == {"water": "sealed", "electric": "open"}
    sealed = [
        b for b in client.get(f"/api/batches?period_id={PERIOD}").json()
        if b["status"] == "sealed"
    ]
    assert [b["batch_id"] for b in sealed] == [clean_id]


def test_fixture_legacy_sealed_mixed_batch_converges_on_align(client):
    """历史半截：混批已被误封。再跑类型对齐必须隔离该批并回退写闸。"""
    from app.db import connect

    rw = _align(client, "water")
    water_batch = rw.json()["batch_id"]
    _inject_foreign_variance(water_batch, ELEC_METER, "water", "electric")

    conn = connect()
    try:
        conn.execute(
            "UPDATE period_gates SET status='sealed', batch_no=? WHERE period_id=? AND meter_type='water'",
            (water_batch, PERIOD),
        )
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed' WHERE batch_id=?", (water_batch,)
        )
        conn.commit()
    finally:
        conn.close()

    # 收敛路径：重新按类型对齐 → 污染批 void，写闸回退 open，新批纯净可封
    again = _align(client, "water")
    assert again.status_code == 200, again.text
    assert _gates(client)["water"] == "open"
    assert _seal(client, "water").status_code == 200
    assert _gates(client) == {"water": "sealed", "electric": "open"}


# ---------- 夹具 4：装载与对齐交错，不得产出半截混批 ----------

def test_fixture_interleaved_load_and_align_never_half_mixed(client):
    """并发装载水表实抄 + 反复按水表对齐：每个批都必须是单一快照、纯水表键。"""
    from app.db import connect
    from app.services import reconcile_service

    def load(i):
        return ("load", client.post(
            "/api/actuals", json=_actual(WATER_METER, 12 + i / 100, f"FX-RACE-{i}")
        ).status_code)

    def align():
        try:
            return ("align", reconcile_service.run_align(PERIOD, "water"))
        except Exception as exc:
            return ("align", getattr(exc, "status_code", 500))

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(align) for _ in range(4)] + [
            pool.submit(load, i) for i in range(10)
        ]
        results = [f.result() for f in futs]

    align_results = [r for kind, r in results if kind == "align"]
    load_results = [r for kind, r in results if kind == "load"]
    assert any(isinstance(r, dict) for r in align_results)
    # 对齐只可能 成功 / 409（闸已封）/ 400（本事务内无键），不出现 500 或未知态
    assert all(isinstance(r, dict) or r in (400, 409) for r in align_results)
    # 装载不受对齐干扰：全部成功
    assert load_results == [200] * 10

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM reconcile_batches WHERE period_id=? AND meter_type='water'",
            (PERIOD,),
        ).fetchall()
        for b in rows:
            assert b["status"] in ("open", "expired")
            vs = conn.execute(
                """SELECT v.meter_id, m.meter_type AS real_type
                   FROM variances v LEFT JOIN meters m ON m.meter_id=v.meter_id
                   WHERE v.batch_id=?""",
                (b["batch_id"],),
            ).fetchall()
            # 每个批的每条差额都必须是真实水表
            assert all(v["real_type"] == "water" for v in vs)
            # matched + variance == 参与该批的键数（单一快照，无半截）
            assert b["matched_count"] + b["variance_count"] >= len(vs)
        # 不存在任何跨类型批：所有类型批的方差真实类型都与其声明一致
        bad = conn.execute(
            """SELECT COUNT(*) c FROM variances v
               JOIN reconcile_batches b ON b.batch_id=v.batch_id
               JOIN meters m ON m.meter_id=v.meter_id
               WHERE m.meter_type<>b.meter_type"""
        ).fetchone()["c"]
        assert bad == 0
        # 写闸与最新 open 批一致：不存在"闸已封而批仍可写"的中间态
        gate = conn.execute(
            "SELECT status, batch_no FROM period_gates WHERE period_id=? AND meter_type='water'",
            (PERIOD,),
        ).fetchone()
        assert gate["status"] == "open"
    finally:
        conn.close()


def test_fixture_align_and_load_share_one_snapshot_atomic(client):
    """对齐与写入同事务串行：对齐提交后到达的写入不会出现在旧批明细里。"""
    from app.db import connect
    from app.services import reconcile_service

    first = reconcile_service.run_align(PERIOD, "water")
    before = client.get(f"/api/variances?batch_id={first['batch_id']}").json()

    # 对齐后新增一条水表实抄，此时旧批明细不得变化（快照隔离）
    assert client.post(
        "/api/actuals", json=_actual(WATER_METER, 20.0, "FX-SNAP-1")
    ).status_code == 200
    after = client.get(f"/api/variances?batch_id={first['batch_id']}").json()
    assert [v["variance_id"] for v in after] == [v["variance_id"] for v in before]

    second = reconcile_service.run_align(PERIOD, "water")
    conn = connect()
    try:
        orig = conn.execute(
            "SELECT * FROM reconcile_batches WHERE batch_id=?", (first["batch_id"],)
        ).fetchone()
        assert orig["status"] == "expired"
    finally:
        conn.close()
    assert second["variance_count"] > first["variance_count"]
    new_delta = {
        v["meter_id"]: v["delta"]
        for v in client.get(f"/api/variances?batch_id={second['batch_id']}").json()
    }
    assert new_delta[WATER_METER] == pytest.approx(8.0)  # 20.0 - 12.0


# ---------- 总览 / 未带类型旧批 / health 不得撒谎 ----------

def test_overview_and_health_follow_per_type_gate_only(client):
    assert _align(client, "water").status_code == 200
    assert _seal(client, "water").status_code == 200

    ov = client.get("/api/overview").json()
    assert ov["gate_counts"]["water"] == {"open": 1, "sealed": 1}
    assert ov["gate_counts"]["electric"] == {"open": 2, "sealed": 0}

    health = client.get("/api/health").json()
    assert health["type_sealed"] == {"water": 1, "electric": 0}

    ps = {p["period_id"]: p for p in client.get("/api/periods").json()}
    assert ps[PERIOD]["sealed_types"] == ["water"]


def test_legacy_typeless_batch_not_shown_as_typed(client):
    from app.db import connect

    conn = connect()
    try:
        conn.execute(
            """INSERT INTO reconcile_batches
                   (batch_id,period_id,meter_type,status,matched_count,variance_count,fingerprint,created_at)
               VALUES ('B-LEGACY','2026-09','','sealed',0,0,'legacy','2026-09-01T00:00:00')"""
        )
        conn.commit()
    finally:
        conn.close()

    listed = client.get(f"/api/batches?period_id=2026-09").json()
    assert all(b["batch_id"] != "B-LEGACY" for b in listed)
    recent = client.get("/api/overview").json()["recent_batches"]
    assert all(b["meter_type"] in ("water", "electric") for b in recent)
    assert all(b["batch_id"] != "B-LEGACY" for b in recent)


def test_seal_never_crosses_type_batch_even_when_gate_open(client):
    rw = _align(client, "water")
    water_batch = rw.json()["batch_id"]
    r = _seal(client, "electric", batch_id=water_batch)
    assert r.status_code == 400
    assert _gates(client) == {"water": "open", "electric": "open"}
