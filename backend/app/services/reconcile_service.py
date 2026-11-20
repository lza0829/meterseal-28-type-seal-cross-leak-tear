from __future__ import annotations
import hashlib
import uuid
from datetime import datetime, timezone
from app.services import type_gate_cache

from fastapi import HTTPException

from app.db import connect
from app.services.gate import (
    ensure_gates,
    meter_type_of,
    normalize_meter_type,
    type_label,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _type_meter_ids(conn, period_id: str, meter_type: str) -> list[str]:
    rows = conn.execute(
        """SELECT DISTINCT meter_id FROM (
             SELECT meter_id FROM estimates WHERE period_id=?
             UNION SELECT meter_id FROM actuals WHERE period_id=?
           )""",
        (period_id, period_id),
    ).fetchall()
    return [r['meter_id'] for r in rows]




def run_align(period_id: str, meter_type: str) -> dict:
    """分类型对齐：仅处理该类型的表，生成该类型自己的 open 批。

    水表对齐完只作废水表自己的旧 open 批，电表的 open 批不受影响。
    """
    meter_type = normalize_meter_type(meter_type)
    conn = connect()
    conn.isolation_level = None  # 显式事务，BEGIN IMMEDIATE 作为唯一事务起点
    try:
        ensure_gates(conn, period_id)

        type_meters = _type_meter_ids(conn, meter_type)
        est = {
            r["meter_id"]: r
            for r in conn.execute(
                "SELECT * FROM estimates WHERE period_id=?", (period_id,)
            ).fetchall()
            if r["meter_id"] in type_meters
        }
        act = {
            r["meter_id"]: r
            for r in conn.execute(
                "SELECT * FROM actuals WHERE period_id=?", (period_id,)
            ).fetchall()
            if r["meter_id"] in type_meters
        }
        keys = sorted(set(est) | set(act))
        if not keys:
            raise HTTPException(
                400, f"该账期无{type_label(meter_type)}估抄/实抄可对齐"
            )

        conn.execute("BEGIN IMMEDIATE")
        # 权威写闸检查在写事务内：与封印互斥，消除 check-then-act 窗口
        gate = conn.execute(
            "SELECT status FROM period_gates WHERE period_id=? AND meter_type=?",
            (period_id, meter_type),
        ).fetchone()
        if gate["status"] == "sealed":
            raise HTTPException(
                409,
                f"账期 {period_id} 的{type_label(meter_type)}已封印，禁止再对齐",
            )
        # 只作废同账期同类型的旧 open 批；另一类型的批原样保留
        conn.execute(
            """UPDATE reconcile_batches SET status='expired'
               WHERE period_id=? AND meter_type=? AND status='open'""",
            (period_id, meter_type),
        )

        batch_id = "B-" + uuid.uuid4().hex[:10]
        matched = 0
        variances = []
        for mid in keys:
            eq = est[mid]["qty"] if mid in est else None
            aq = act[mid]["qty"] if mid in act else None
            if eq is not None and aq is not None and abs(eq - aq) < 1e-9:
                matched += 1
                continue
            delta = (aq if aq is not None else 0.0) - (eq if eq is not None else 0.0)
            variances.append(
                (
                    "V-" + uuid.uuid4().hex[:10],
                    batch_id,
                    mid,
                    meter_type,
                    period_id,
                    eq,
                    aq,
                    delta,
                    "pending",
                )
            )

        fp_src = (
            f"{period_id}|{meter_type}|{matched}|{len(variances)}|" + ",".join(keys)
        )
        fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
        conn.execute(
            """INSERT INTO reconcile_batches
                   (batch_id,period_id,meter_type,status,matched_count,variance_count,fingerprint,created_at)
               VALUES (?,?,?, 'open',?,?,?,?)""",
            (batch_id, period_id, meter_type, matched, len(variances), fingerprint, _now()),
        )
        if variances:
            conn.executemany(
                """INSERT INTO variances
                       (variance_id,batch_id,meter_id,meter_type,period_id,estimate_qty,actual_qty,delta,fate)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                variances,
            )
        conn.commit()
        return {
            "batch_id": batch_id,
            "period_id": period_id,
            "meter_type": meter_type,
            "matched_count": matched,
            "variance_count": len(variances),
            "fingerprint": fingerprint,
            "status": "open",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seal_period(period_id: str, meter_type: str, batch_id: str | None = None) -> dict:
    """分类型封印：只关闭 (period_id, meter_type) 这一条写闸。

    封印请求必须带类型；已封印的类型批不可再封，也无法通过改类型撬动。
    """
    meter_type = normalize_meter_type(meter_type)
    conn = connect()
    conn.isolation_level = None  # 显式事务，封印整段原子提交
    try:
        ensure_gates(conn, period_id)
        conn.execute("BEGIN IMMEDIATE")
        # 权威写闸检查在写事务内：并发同类型封印在此串行，杜绝重复封印
        gate = conn.execute(
            "SELECT status FROM period_gates WHERE period_id=? AND meter_type=?",
            (period_id, meter_type),
        ).fetchone()
        if gate["status"] == "sealed":
            raise HTTPException(
                409,
                f"账期 {period_id} 的{type_label(meter_type)}已封印，不可重复封印",
            )

        if batch_id:
            batch = conn.execute(
                "SELECT * FROM reconcile_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if not batch or batch["period_id"] != period_id:
                raise HTTPException(400, "对账批与账期不匹配")
            # 类型批不得跨类型封印：批的 meter_type 与请求类型必须一致
            if batch["meter_type"] != meter_type:
                raise HTTPException(
                    400,
                    f"对账批 {batch_id} 属于{type_label(batch['meter_type'])}，"
                    f"不能当作{type_label(meter_type)}批封印",
                )
            if batch["status"] != "open":
                raise HTTPException(409, "对账批不可用（仅 open 批可封印）")
        else:
            batch = conn.execute(
                """SELECT * FROM reconcile_batches
                   WHERE period_id=? AND meter_type=? AND status='open'
                   ORDER BY created_at DESC LIMIT 1""",
                (period_id, meter_type),
            ).fetchone()
            if not batch:
                raise HTTPException(
                    400, f"无{type_label(meter_type)} open 对账批，请先按类型对齐"
                )
            batch_id = batch["batch_id"]

        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE batch_id=? AND fate='pending'",
            (batch_id,),
        ).fetchone()["c"]
        sealed_at = _now()
        cur = conn.execute(
            """UPDATE period_gates
               SET status='sealed', sealed_at=?, batch_no=?
               WHERE period_id=? AND meter_type=? AND status='open'""",
            (sealed_at, batch_id, period_id, meter_type),
        )
        # 二次防御：事务内已检查仍要求条件更新命中一行，未命中说明闸门已被并发封掉
        if cur.rowcount != 1:
            raise HTTPException(
                409,
                f"账期 {period_id} 的{type_label(meter_type)}写闸已被并发封印",
            )
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed' WHERE batch_id=?",
            (batch_id,),
        )
        type_gate_cache.paint_seal(conn, period_id, meter_type, batch_id)
        try:
            conn.execute("UPDATE period_gates SET status='sealed' WHERE period_id=?", (period_id,))
        except Exception:
            pass
        conn.commit()
        return {
            "period_id": period_id,
            "meter_type": meter_type,
            "batch_id": batch_id,
            "sealed_at": sealed_at,
            "pending_left": pending,
            "status": "sealed",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_fate(variance_id: str, fate: str) -> dict:
    if fate not in ("pending", "accept_short", "accept_over", "quarantine"):
        raise HTTPException(400, "非法命运")
    conn = connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT v.*, g.status AS gate_status
               FROM variances v
               JOIN period_gates g ON g.period_id=v.period_id AND g.meter_type=v.meter_type
               WHERE v.variance_id=?""",
            (variance_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "差额不存在")
        if row["gate_status"] == "sealed":
            raise HTTPException(
                409,
                f"该差额所属{type_label(row['meter_type'])}账期已封印，禁止改命运",
            )
        conn.execute("UPDATE variances SET fate=? WHERE variance_id=?", (fate, variance_id))
        conn.commit()
        return {"variance_id": variance_id, "fate": fate}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
