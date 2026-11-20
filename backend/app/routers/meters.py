from __future__ import annotations
from fastapi import APIRouter
from app.db import connect

router = APIRouter(tags=["meters"])


@router.get("/meters")
def list_meters():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM meters ORDER BY meter_id").fetchall()]
    finally:
        conn.close()
