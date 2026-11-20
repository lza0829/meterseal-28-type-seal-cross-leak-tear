from __future__ import annotations
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect
from app.services.gate import assert_type_writable, meter_type_of

router = APIRouter(tags=["readings"])


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class EstimateIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    doc_no: str = Field(min_length=1)


class ActualIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    occurred_at: str
    reader_code: str = "R00"
    doc_no: str = Field(min_length=1)


@router.get("/estimates")
def list_estimates(period_id: str | None = None, meter_type: str | None = None):
    conn = connect()
    try:
        sql = (
            "SELECT e.*, m.meter_type FROM estimates e"
            " JOIN meters m ON m.meter_id=e.meter_id WHERE 1=1"
        )
        args: list = []
        if period_id:
            sql += " AND e.period_id=?"
            args.append(period_id)
        if meter_type:
            sql += " AND m.meter_type=?"
            args.append(meter_type)
        sql += " ORDER BY e.period_id, e.meter_id"
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/estimates")
def create_estimate(body: EstimateIn):
    conn = connect()
    conn.isolation_level = None  # 显式事务：闸门检查与写入同事务，与封印互斥
    try:
        meter_type = meter_type_of(conn, body.meter_id)
        conn.execute("BEGIN IMMEDIATE")
        assert_type_writable(conn, body.period_id, meter_type)
        eid = "E-" + uuid.uuid4().hex[:10]
        try:
            conn.execute(
                """INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at)
                   VALUES (?,?,?,?,?,?)""",
                (eid, body.meter_id, body.period_id, body.qty, body.doc_no, _now()),
            )
            conn.commit()
        except Exception as e:
            raise HTTPException(409, f"估抄写入失败: {e}") from e
        return {"estimate_id": eid, "meter_type": meter_type, "ok": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/actuals")
def list_actuals(period_id: str | None = None, meter_type: str | None = None):
    conn = connect()
    try:
        sql = (
            "SELECT a.*, m.meter_type FROM actuals a"
            " JOIN meters m ON m.meter_id=a.meter_id WHERE 1=1"
        )
        args: list = []
        if period_id:
            sql += " AND a.period_id=?"
            args.append(period_id)
        if meter_type:
            sql += " AND m.meter_type=?"
            args.append(meter_type)
        sql += " ORDER BY a.period_id, a.meter_id"
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/actuals")
def create_actual(body: ActualIn):
    conn = connect()
    conn.isolation_level = None  # 显式事务：闸门检查与写入同事务，与封印互斥
    try:
        meter_type = meter_type_of(conn, body.meter_id)
        conn.execute("BEGIN IMMEDIATE")
        assert_type_writable(conn, body.period_id, meter_type)
        aid = "A-" + uuid.uuid4().hex[:10]
        try:
            conn.execute(
                """INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    aid,
                    body.meter_id,
                    body.period_id,
                    body.qty,
                    body.occurred_at,
                    body.reader_code,
                    body.doc_no,
                    _now(),
                ),
            )
            conn.commit()
        except Exception as e:
            raise HTTPException(409, f"实抄写入失败: {e}") from e
        return {"actual_id": aid, "meter_type": meter_type, "ok": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
