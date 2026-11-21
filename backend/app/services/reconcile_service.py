from __future__ import annotations
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect
from app.services.gate import (
    METER_TYPES,
    ensure_gates,
    normalize_meter_type,
    type_label,
)


# ---------------------------------------------------------------------------
# 全局唯一口径（声明架构）：
#   封印请求里的 meter_type 是权威声明。
#   1) 批次明细键必须全部是该声明类型的真实表（join meters 判定，不信批次自报）；
#   2) 发现异类型键：不得留下 sealed 批，也不得混进新批——直接回滚并报错；
#   3) 写闸只关闭 (period_id, meter_type) 一行，绝不做整期 UPDATE；
#   4) 总览计数只读 period_gates，不读任何旁路缓存；
#   5) 装载、对齐、封印一律 BEGIN IMMEDIATE 串行，半截混批不得提交。
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _typed_keys(conn, period_id: str, meter_type: str) -> tuple[dict, dict]:
    """只取该账期、真实类型 == meter_type 的估抄/实抄，按表号索引。

    表类型以 meters.meter_type 为准，拒绝"读数行自报类型"造成的混桶。
    """
    est = {
        r["meter_id"]: r
        for r in conn.execute(
            """SELECT e.* FROM estimates e
               JOIN meters m ON m.meter_id=e.meter_id
               WHERE e.period_id=? AND m.meter_type=?
               ORDER BY e.loaded_at, e.estimate_id""",
            (period_id, meter_type),
        ).fetchall()
    }
    act = {
        r["meter_id"]: r
        for r in conn.execute(
            """SELECT a.* FROM actuals a
               JOIN meters m ON m.meter_id=a.meter_id
               WHERE a.period_id=? AND m.meter_type=?
               ORDER BY a.loaded_at, a.actual_id""",
            (period_id, meter_type),
        ).fetchall()
    }
    return est, act


def _batch_foreign_keys(conn, batch_id: str, meter_type: str) -> list[str]:
    """批明细里真实类型与声明不一致（或表具不存在）的键。

    以 meters.meter_type 为准，不信 variance 自报的 meter_type。
    """
    rows = conn.execute(
        """SELECT DISTINCT v.meter_id, m.meter_id AS known, m.meter_type AS real_type
           FROM variances v
           LEFT JOIN meters m ON m.meter_id=v.meter_id
           WHERE v.batch_id=?
             AND (m.meter_id IS NULL OR m.meter_type<>?)
           ORDER BY v.meter_id""",
        (batch_id, meter_type),
    ).fetchall()
    return [r["meter_id"] for r in rows]


def _sanitize_type_batches(conn, period_id: str, meter_type: str) -> list[str]:
    """把同账期同类型下已混入异类型键的批隔离为 void，返回被隔离批号。

    混合批不得长期现行：作废（隔离）而不是悄悄留下，更不能带进新批。
    若被隔离的正是该类型写闸绑定的批，写闸一并回退为 open——闸与明细
    必须互相印证，不能出现"闸封着，绑定的批却已作废"的撒谎状态。
    """
    rows = conn.execute(
        """SELECT DISTINCT v.batch_id FROM variances v
           LEFT JOIN meters m ON m.meter_id=v.meter_id
           JOIN reconcile_batches b ON b.batch_id=v.batch_id
           WHERE b.period_id=? AND b.meter_type=? AND b.status<>'void'
             AND (m.meter_id IS NULL OR m.meter_type<>?)
           ORDER BY v.batch_id""",
        (period_id, meter_type, meter_type),
    ).fetchall()
    polluted = [r["batch_id"] for r in rows]
    if not polluted:
        return []
    conn.executemany(
        "UPDATE reconcile_batches SET status='void' WHERE batch_id=?",
        [(b,) for b in polluted],
    )
    marks = ",".join("?" for _ in polluted)
    conn.execute(
        f"""UPDATE period_gates
            SET status='open', sealed_at=NULL, batch_no=NULL
            WHERE period_id=? AND meter_type=? AND status='sealed'
              AND batch_no IN ({marks})""",
        [period_id, meter_type, *polluted],
    )
    return polluted


def run_align(period_id: str, meter_type: str) -> dict:
    """分类型对齐：只处理该类型的表，生成该类型自己的 open 批。

    水表对齐完只作废水表自己的旧 open 批，电表的 open 批不受影响。
    读数快照在写事务内读取：装载与对齐互斥，不把半截装载切进批。
    """
    meter_type = normalize_meter_type(meter_type)
    conn = connect()
    conn.isolation_level = None  # 显式事务，BEGIN IMMEDIATE 作为唯一事务起点
    try:
        ensure_gates(conn, period_id)
        conn.execute("BEGIN IMMEDIATE")

        # 先收敛历史半截混批：已混入异类型键的批（含被误封的）一律隔离为 void，
        # 若写闸恰好绑着它则回退为 open。这样"闸说封了、明细却是混的"不会长期存在。
        _sanitize_type_batches(conn, period_id, meter_type)

        # 权威写闸检查在写事务内：与封印互斥，消除 check-then-act 窗口
        gate = conn.execute(
            "SELECT status FROM period_gates WHERE period_id=? AND meter_type=?",
            (period_id, meter_type),
        ).fetchone()
        if gate and gate["status"] == "sealed":
            raise HTTPException(
                409,
                f"账期 {period_id} 的{type_label(meter_type)}已封印，禁止再对齐",
            )

        # 先读后写都必须在本事务内，避免装载交错出半截类型批
        est, act = _typed_keys(conn, period_id, meter_type)
        keys = sorted(set(est) | set(act))
        if not keys:
            raise HTTPException(
                400, f"该账期无{type_label(meter_type)}估抄/实抄可对齐"
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
        # 提交前自检：新批明细必须纯该类型，否则整批回滚（不留半截混批）
        bad = _batch_foreign_keys(conn, batch_id, meter_type)
        if bad:
            raise HTTPException(
                409,
                f"对齐结果混入非{type_label(meter_type)}键：{', '.join(bad)}；整批回滚",
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
    批明细若含异类型键，封印整段回滚：sealed 批不得混类型，闸与明细都不撒谎。
    """
    meter_type = normalize_meter_type(meter_type)
    conn = connect()
    conn.isolation_level = None  # 显式事务，封印整段原子提交
    try:
        ensure_gates(conn, period_id)
        conn.execute("BEGIN IMMEDIATE")

        # 历史遗留的半截混批（明细含异类型键）先隔离为 void 并回退误封的写闸，
        # 使"闸说封了、批明细却混着另一类型"的撒谎状态收敛，而不是继续现行。
        polluted = _sanitize_type_batches(conn, period_id, meter_type)
        polluted_detail = {
            bid: _batch_foreign_keys(conn, bid, meter_type) for bid in polluted
        }

        # 权威写闸检查在写事务内：并发同类型封印在此串行，杜绝重复封印
        gate = conn.execute(
            "SELECT status FROM period_gates WHERE period_id=? AND meter_type=?",
            (period_id, meter_type),
        ).fetchone()
        if gate and gate["status"] == "sealed":
            raise HTTPException(
                409,
                f"账期 {period_id} 的{type_label(meter_type)}已封印，不可重复封印",
            )

        if batch_id:
            if batch_id in polluted_detail:
                raise HTTPException(
                    409,
                    f"对账批 {batch_id} 明细混入非{type_label(meter_type)}键："
                    f"{', '.join(polluted_detail[batch_id])}；该批已隔离，拒绝封印",
                )
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
                if polluted_detail:
                    keys = sorted({k for ks in polluted_detail.values() for k in ks})
                    raise HTTPException(
                        409,
                        f"该账期{type_label(meter_type)}批明细混入非"
                        f"{type_label(meter_type)}键：{', '.join(keys)}；"
                        f"混批已隔离，请重新按类型对齐后再封印",
                    )
                raise HTTPException(
                    400, f"无{type_label(meter_type)} open 对账批，请先按类型对齐"
                )
            batch_id = batch["batch_id"]

        # 明细自检：异类型键不得留在 sealed 批里；发现即回滚，不封半截混批
        bad = _batch_foreign_keys(conn, batch_id, meter_type)
        if bad:
            raise HTTPException(
                409,
                f"对账批 {batch_id} 明细混入非{type_label(meter_type)}键："
                f"{', '.join(bad)}；拒绝封印，请重新按类型对齐",
            )

        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE batch_id=? AND fate='pending'",
            (batch_id,),
        ).fetchone()["c"]
        sealed_at = _now()
        # 只关这一条类型闸，绝不 UPDATE 整期
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
        other_status = {
            r["meter_type"]: r["status"]
            for r in conn.execute(
                "SELECT meter_type, status FROM period_gates WHERE period_id=?",
                (period_id,),
            ).fetchall()
        }
        conn.commit()
        return {
            "period_id": period_id,
            "meter_type": meter_type,
            "batch_id": batch_id,
            "sealed_at": sealed_at,
            "pending_left": pending,
            "status": "sealed",
            "gates": {t: other_status.get(t, "open") for t in METER_TYPES},
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
        # 差额归属与写闸都以表具真实类型为准：不能靠改 variance 行内类型绕闸
        row = conn.execute(
            """SELECT v.variance_id, v.fate,
                      COALESCE(m.meter_type, v.meter_type) AS real_type,
                      g.status AS gate_status
               FROM variances v
               LEFT JOIN meters m ON m.meter_id=v.meter_id
               LEFT JOIN period_gates g
                      ON g.period_id=v.period_id
                     AND g.meter_type=COALESCE(m.meter_type, v.meter_type)
               WHERE v.variance_id=?""",
            (variance_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "差额不存在")
        if row["gate_status"] == "sealed":
            raise HTTPException(
                409,
                f"该差额所属{type_label(row['real_type'])}账期已封印，禁止改命运",
            )
        conn.execute("UPDATE variances SET fate=? WHERE variance_id=?", (fate, variance_id))
        conn.commit()
        return {"variance_id": variance_id, "fate": fate}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
