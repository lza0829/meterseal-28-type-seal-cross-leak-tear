from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.db import connect
from app.services import reconcile_service
from app.services.gate import METER_TYPES

router = APIRouter(tags=["reconcile"])


class AlignIn(BaseModel):
    period_id: str
    # 分类型架构：对齐必须声明表类型，缺类型直接 422，不允许混桶
    meter_type: str = Field(...)


class SealIn(BaseModel):
    period_id: str
    # 封印必须带类型：只关闭该类型写闸，禁止未带类型却宣称分类型封
    meter_type: str = Field(...)
    batch_id: str | None = None


class FateIn(BaseModel):
    fate: str


@router.post("/align")
def align(body: AlignIn):
    return reconcile_service.run_align(body.period_id, body.meter_type)


@router.get("/batches")
def batches(period_id: str | None = None, meter_type: str | None = None):
    """类型批列表：可按账期+类型过滤，每行带 seal 同口径的 meter_type。"""
    conn = connect()
    try:
        sql = "SELECT * FROM reconcile_batches WHERE 1=1"
        args: list = []
        if period_id:
            sql += " AND period_id=?"
            args.append(period_id)
        if meter_type:
            sql += " AND meter_type=?"
            args.append(meter_type)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/variances")
def variances(
    batch_id: str | None = None,
    period_id: str | None = None,
    meter_type: str | None = None,
):
    """差额快照带 meter_type；过滤口径与分类型写闸一致。"""
    conn = connect()
    try:
        sql = "SELECT * FROM variances WHERE 1=1"
        args: list = []
        if batch_id:
            sql += " AND batch_id=?"
            args.append(batch_id)
        if period_id:
            sql += " AND period_id=?"
            args.append(period_id)
        if meter_type:
            sql += " AND meter_type=?"
            args.append(meter_type)
        sql += " ORDER BY meter_type, meter_id"
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/variances/{variance_id}/fate")
def set_fate(variance_id: str, body: FateIn):
    return reconcile_service.set_fate(variance_id, body.fate)


@router.post("/seal")
def seal(body: SealIn):
    return reconcile_service.seal_period(body.period_id, body.meter_type, body.batch_id)


@router.get("/periods")
def periods():
    """账期总览：每个账期附水/电各自写闸状态，计数与写闸同源。"""
    conn = connect()
    try:
        ps = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM periods ORDER BY period_id DESC"
            ).fetchall()
        ]
        for p in ps:
            gates = conn.execute(
                "SELECT meter_type, status, sealed_at, batch_no FROM period_gates WHERE period_id=?",
                (p["period_id"],),
            ).fetchall()
            by_type = {g["meter_type"]: dict(g) for g in gates}
            p["gates"] = [by_type.get(t, {"meter_type": t, "status": "open"}) for t in METER_TYPES]
            p["sealed_types"] = [
                g["meter_type"] for g in p["gates"] if g["status"] == "sealed"
            ]
        return ps
    finally:
        conn.close()
