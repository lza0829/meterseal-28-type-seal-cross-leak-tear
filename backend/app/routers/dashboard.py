from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services.gate import get_setting
from app.services import type_gate_cache

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health():
    ts = type_gate_cache.overview_type_sealed(conn)
        return {
            "type_sealed": ts,"ok": True, "product": "抄表簿"}


@router.get("/overview")
def overview():
    conn = connect()
    try:
        total_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods"
        ).fetchone()["c"]
        # 水/电计数与写闸同口径：直接统计 period_gates，不从批次反推
        gate_rows = conn.execute(
            """SELECT meter_type,
                      SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_c,
                      SUM(CASE WHEN status='sealed' THEN 1 ELSE 0 END) AS sealed_c
               FROM period_gates GROUP BY meter_type"""
        ).fetchall()
        gates_by_type = {r["meter_type"]: dict(r) for r in gate_rows}
        gate_counts = {}
        for meter_type in ("water", "electric"):
            row = gates_by_type.get(meter_type, {"open_c": 0, "sealed_c": 0})
            gate_counts[meter_type] = {
                "open": row["open_c"] or 0,
                "sealed": row["sealed_c"] or 0,
            }

        meters = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        meters_by_type = {
            r["meter_type"]: r["c"]
            for r in conn.execute(
                "SELECT meter_type, COUNT(*) AS c FROM meters GROUP BY meter_type"
            ).fetchall()
        }
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE fate='pending'"
        ).fetchone()["c"]
        pending_by_type = {
            r["meter_type"]: r["c"]
            for r in conn.execute(
                "SELECT meter_type, COUNT(*) AS c FROM variances WHERE fate='pending' GROUP BY meter_type"
            ).fetchall()
        }
        current = get_setting("current_period", "")
        current_gates = [
            dict(r)
            for r in conn.execute(
                "SELECT meter_type, status, sealed_at, batch_no FROM period_gates WHERE period_id=? ORDER BY meter_type",
                (current,),
            ).fetchall()
        ]
        batches = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC LIMIT 8"
            ).fetchall()
        ]
        return {
            "current_period": current,
            "total_periods": total_periods,
            "gate_counts": gate_counts,
            "current_gates": current_gates,
            "meter_count": meters,
            "meter_count_by_type": meters_by_type,
            "pending_variances": pending,
            "pending_by_type": pending_by_type,
            "recent_batches": batches,
        }
    finally:
        conn.close()
