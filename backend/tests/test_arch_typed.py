"""架构 A（按 meter_type 分桶对齐封印）核对。

覆盖题述全链路联动：
类型过滤 / 对齐分桶 / 封印入口 / 写闸 / 差额快照 / 总览 / 类型批列表。
"""
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

PERIOD = "2026-10"
WATER_METER = "M-W-101"
ELEC_METER = "M-E-101"


def _align(client: TestClient, meter_type: str):
    return client.post("/api/align", json={"period_id": PERIOD, "meter_type": meter_type})


def _seal(client: TestClient, meter_type: str, batch_id=None):
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


# ---------- 入口强制类型 ----------

def test_align_without_meter_type_rejected(client):
    r = client.post("/api/align", json={"period_id": PERIOD})
    assert r.status_code == 422


def test_seal_without_meter_type_rejected(client):
    r = client.post("/api/seal", json={"period_id": PERIOD})
    assert r.status_code == 422


def test_seal_with_illegal_meter_type_rejected(client):
    r = client.post("/api/seal", json={"period_id": PERIOD, "meter_type": "gas"})
    assert r.status_code == 400


def test_align_unknown_period_404(client):
    r = client.post("/api/align", json={"period_id": "1999-01", "meter_type": "water"})
    assert r.status_code == 404


# ---------- 分桶对齐：水/电各自成批，互不混用 ----------

def test_align_buckets_are_type_segregated(client):
    rw = _align(client, "water")
    assert rw.status_code == 200, rw.text
    water_batch = rw.json()
    assert water_batch["meter_type"] == "water"

    re_ = _align(client, "electric")
    assert re_.status_code == 200, re_.text
    elec_batch = re_.json()
    assert elec_batch["meter_type"] == "electric"
    assert water_batch["batch_id"] != elec_batch["batch_id"]

    # 差额快照必须带 seal_type(meter_type)，且只含本类型表
    vw = client.get(f"/api/variances?batch_id={water_batch['batch_id']}").json()
    assert vw and all(v["meter_type"] == "water" for v in vw)
    assert all(v["meter_id"].startswith("M-W") for v in vw)
    ve = client.get(f"/api/variances?batch_id={elec_batch['batch_id']}").json()
    assert ve and all(v["meter_type"] == "electric" for v in ve)
    assert all(v["meter_id"].startswith("M-E") for v in ve)


def test_align_water_does_not_expire_electric_open_batch(client):
    _align(client, "electric")
    open_e_before = client.get(
        f"/api/batches?period_id={PERIOD}&meter_type=electric"
    ).json()
    assert [b for b in open_e_before if b["status"] == "open"]

    rw = _align(client, "water")
    assert rw.status_code == 200

    open_e_after = client.get(
        f"/api/batches?period_id={PERIOD}&meter_type=electric"
    ).json()
    assert [b for b in open_e_after if b["status"] == "open"], "水表对齐不得作废电表 open 批"

    # 同类型重复对齐：只作废自己类型的旧 open 批
    _align(client, "water")
    water_batches = client.get(
        f"/api/batches?period_id={PERIOD}&meter_type=water"
    ).json()
    open_w = [b for b in water_batches if b["status"] == "open"]
    assert len(open_w) == 1
    # 电表批仍在
    assert [b for b in open_e_after if b["status"] == "open"]


def test_align_empty_type_bucket_400(client):
    r = client.post(
        "/api/align", json={"period_id": "2026-09", "meter_type": "water"}
    )
    assert r.status_code == 400


# ---------- 封印只关该类型写闸 ----------

def test_seal_water_electric_still_writable(client):
    """架构 A 核心口径：封水表后电表仍可写实抄。"""
    _align(client, "water")
    rs = _seal(client, "water")
    assert rs.status_code == 200, rs.text
    assert rs.json()["meter_type"] == "water"

    # 水表写估抄/实抄均被拒
    r1 = client.post("/api/actuals", json=_actual(WATER_METER, 99, "ACT-W-NEW"))
    assert r1.status_code == 409
    assert "水表" in r1.json()["detail"]
    r2 = client.post("/api/estimates", json=_estimate(WATER_METER, 99, "EST-W-NEW"))
    assert r2.status_code == 409

    # 电表仍可写实抄（电表未齐，不能因为水表封了被整期关闸）
    r3 = client.post("/api/actuals", json=_actual(ELEC_METER, 222, "ACT-E-NEW"))
    assert r3.status_code == 200, r3.text
    r4 = client.post("/api/estimates", json=_estimate(ELEC_METER, 222, "EST-E-NEW"))
    assert r4.status_code == 200, r4.text


def test_seal_electric_then_water_still_writable(client):
    _align(client, "electric")
    assert _seal(client, "electric").status_code == 200

    r = client.post("/api/actuals", json=_actual(ELEC_METER, 1, "ACT-E-X"))
    assert r.status_code == 409
    assert "电表" in r.json()["detail"]

    r2 = client.post("/api/actuals", json=_actual(WATER_METER, 13, "ACT-W-X"))
    assert r2.status_code == 200


def test_seal_requires_open_batch_of_that_type(client):
    # 水表没有 open 批时封印被拒，且不会误伤电表
    r = _seal(client, "water")
    assert r.status_code == 400
    # 电表此时照样可写
    assert client.post("/api/actuals", json=_actual(ELEC_METER, 1, "ACT-E-Y"))\
        .status_code == 200


def test_sealed_type_cannot_be_resealed(client):
    _align(client, "water")
    assert _seal(client, "water").status_code == 200
    r = _seal(client, "water")
    assert r.status_code == 409


def test_align_rejected_after_type_sealed(client):
    _align(client, "water")
    _seal(client, "water")
    r = _align(client, "water")
    assert r.status_code == 409
    # 另一类型对齐不受影响
    assert _align(client, "electric").status_code == 200


def test_batch_cannot_be_sealed_under_wrong_type(client):
    """已 sealed/open 的类型批不得被事后改类型撬动。"""
    rw = _align(client, "water")
    water_batch_id = rw.json()["batch_id"]
    # 拿水表批却按电表封：拒绝
    r = _seal(client, "electric", batch_id=water_batch_id)
    assert r.status_code == 400
    assert "类型" in r.json()["detail"] or "电表" in r.json()["detail"]

    # 正常封水表批
    assert _seal(client, "water", batch_id=water_batch_id).status_code == 200
    # 已封印批换类型再封：同样拒绝（批已非 open + 类型不符）
    r2 = _seal(client, "electric", batch_id=water_batch_id)
    assert r2.status_code in (400, 409)


def test_fate_blocked_only_by_own_type_gate(client):
    rw = _align(client, "water")
    re_ = _align(client, "electric")
    water_vid = client.get(
        f"/api/variances?batch_id={rw.json()['batch_id']}"
    ).json()[0]["variance_id"]
    elec_vid = client.get(
        f"/api/variances?batch_id={re_.json()['batch_id']}"
    ).json()[0]["variance_id"]

    _seal(client, "water")
    # 水表差额禁改命运
    r = client.post(f"/api/variances/{water_vid}/fate", json={"fate": "quarantine"})
    assert r.status_code == 409
    # 电表差额仍可处置
    r2 = client.post(f"/api/variances/{elec_vid}/fate", json={"fate": "accept_over"})
    assert r2.status_code == 200
    assert r2.json()["fate"] == "accept_over"


# ---------- 总览计数与写闸一致 ----------

def test_overview_counts_match_gates(client):
    ov0 = client.get("/api/overview").json()
    assert ov0["gate_counts"]["water"] == {"open": 2, "sealed": 0}
    assert ov0["gate_counts"]["electric"] == {"open": 2, "sealed": 0}
    assert {g["meter_type"]: g["status"] for g in ov0["current_gates"]} == {
        "water": "open",
        "electric": "open",
    }

    _align(client, "water")
    _seal(client, "water")

    ov1 = client.get("/api/overview").json()
    assert ov1["gate_counts"]["water"] == {"open": 1, "sealed": 1}
    # 电闸门计数不受水封印影响
    assert ov1["gate_counts"]["electric"] == {"open": 2, "sealed": 0}
    current = {g["meter_type"]: g["status"] for g in ov1["current_gates"]}
    assert current == {"water": "sealed", "electric": "open"}

    # /periods 口径与 overview 一致
    ps = {p["period_id"]: p for p in client.get("/api/periods").json()}
    gates = {g["meter_type"]: g["status"] for g in ps[PERIOD]["gates"]}
    assert gates == {"water": "sealed", "electric": "open"}
    assert ps[PERIOD]["sealed_types"] == ["water"]


def test_batches_list_uses_same_type_lens(client):
    rw = _align(client, "water")
    _align(client, "electric")
    water_only = client.get(
        f"/api/batches?period_id={PERIOD}&meter_type=water"
    ).json()
    assert water_only and all(b["meter_type"] == "water" for b in water_only)
    elec_only = client.get(
        f"/api/batches?period_id={PERIOD}&meter_type=electric"
    ).json()
    assert elec_only and all(b["meter_type"] == "electric" for b in elec_only)

    # 封印后该批状态与写闸提示同口径：批 sealed、闸门 sealed
    _seal(client, "water", batch_id=rw.json()["batch_id"])
    water_after = client.get(
        f"/api/batches?period_id={PERIOD}&meter_type=water"
    ).json()
    assert water_after[0]["status"] == "sealed"
    from app.db import connect
    conn = connect()
    try:
        gate = conn.execute(
            "SELECT status FROM period_gates WHERE period_id=? AND meter_type='water'",
            (PERIOD,),
        ).fetchone()
        assert gate["status"] == "sealed"
    finally:
        conn.close()


def test_variances_filter_by_type(client):
    _align(client, "water")
    _align(client, "electric")
    wv = client.get(f"/api/variances?period_id={PERIOD}&meter_type=water").json()
    ev = client.get(f"/api/variances?period_id={PERIOD}&meter_type=electric").json()
    assert all(v["meter_type"] == "water" for v in wv)
    assert all(v["meter_type"] == "electric" for v in ev)
    assert {v["meter_id"] for v in wv}.isdisjoint({v["meter_id"] for v in ev})


# ---------- 并发：两类同时封不得互相覆盖、不留半截 ----------

def test_concurrent_seals_both_types_independent(client):
    # 直接驱动 service 层：每条线程独立连接，对齐后并发封印水/电
    from app.services import reconcile_service
    from app.db import connect

    reconcile_service.run_align(PERIOD, "water")
    reconcile_service.run_align(PERIOD, "electric")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(reconcile_service.seal_period, PERIOD, "water"),
            pool.submit(reconcile_service.seal_period, PERIOD, "electric"),
        ]
        results = [f.result() for f in futs]

    assert sorted(r["meter_type"] for r in results) == ["electric", "water"]
    assert all(r["status"] == "sealed" for r in results)

    conn = connect()
    try:
        gates = {
            r["meter_type"]: r["status"]
            for r in conn.execute(
                "SELECT meter_type, status FROM period_gates WHERE period_id=?",
                (PERIOD,),
            ).fetchall()
        }
        assert gates == {"water": "sealed", "electric": "sealed"}
        # 两条闸门各绑各的批，无覆盖
        batch_nos = {
            r["meter_type"]: r["batch_no"]
            for r in conn.execute(
                "SELECT meter_type, batch_no FROM period_gates WHERE period_id=?",
                (PERIOD,),
            ).fetchall()
        }
        assert batch_nos["water"] != batch_nos["electric"]
        # 无半截类型批：每个类型恰好一个 sealed 批、无 open 残留
        for t in ("water", "electric"):
            rows = conn.execute(
                "SELECT status, COUNT(*) c FROM reconcile_batches WHERE period_id=? AND meter_type=? GROUP BY status",
                (PERIOD, t),
            ).fetchall()
            counts = {r["status"]: r["c"] for r in rows}
            assert counts.get("sealed") == 1
            assert counts.get("open", 0) == 0
    finally:
        conn.close()

    # 两类都封后两类都拒写
    assert client.post("/api/actuals", json=_actual(WATER_METER, 1, "ACT-C1"))\
        .status_code == 409
    assert client.post("/api/actuals", json=_actual(ELEC_METER, 1, "ACT-C2"))\
        .status_code == 409


def test_concurrent_double_seal_same_type_serialized(client):
    from app.services import reconcile_service
    from app.db import connect

    reconcile_service.run_align(PERIOD, "water")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(reconcile_service.seal_period, PERIOD, "water"),
            pool.submit(reconcile_service.seal_period, PERIOD, "water"),
        ]
        statuses = []
        for f in futs:
            try:
                f.result()
                statuses.append(200)
            except Exception as exc:  # HTTPException
                statuses.append(exc.status_code)
    assert sorted(statuses) == [200, 409]

    conn = connect()
    try:
        sealed = conn.execute(
            "SELECT COUNT(*) c FROM period_gates WHERE period_id=? AND meter_type='water' AND status='sealed'",
            (PERIOD,),
        ).fetchone()["c"]
        assert sealed == 1
    finally:
        conn.close()


def test_concurrent_writes_against_seal_no_leak(client):
    """封印与同类型写入并发：写事务与封印事务互斥，已封后不得漏写。"""
    from concurrent.futures import ThreadPoolExecutor
    from app.services import reconcile_service
    from app.db import connect

    reconcile_service.run_align(PERIOD, "water")

    def write_water(idx):
        # 走 HTTP 完整栈：create_actual 的 BEGIN IMMEDIATE 与封印互斥
        return client.post(
            "/api/actuals",
            json=_actual(WATER_METER, 100 + idx, f"ACT-RACE-{idx}"),
        ).status_code

    def seal_water():
        try:
            reconcile_service.seal_period(PERIOD, "water")
            return 200
        except Exception as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(seal_water)] + [
            pool.submit(write_water, i) for i in range(8)
        ]
        codes = [f.result() for f in futs]

    assert 200 in codes  # 封印成功一次
    # 封印后到达的写入必须全部 409；不存在既封了闸又漏写成功的状态
    conn = connect()
    try:
        gate = conn.execute(
            "SELECT status FROM period_gates WHERE period_id=? AND meter_type='water'",
            (PERIOD,),
        ).fetchone()
        assert gate["status"] == "sealed"
        accepted = conn.execute(
            "SELECT COUNT(*) c FROM actuals WHERE period_id=? AND meter_id=? AND doc_no LIKE 'ACT-RACE-%'",
            (PERIOD, WATER_METER),
        ).fetchone()["c"]
        # 200 状态码总数（1 个封印 + 被放行的写入）必须与实际落库行数一致
        assert codes.count(200) == 1 + accepted
        # 封印后再写必然被拒
        assert write_water(99) == 409
    finally:
        conn.close()
