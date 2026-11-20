from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import connect

router = APIRouter(tags=["settings"])


class SettingIn(BaseModel):
    value: str


@router.get("/settings")
def list_settings():
    conn = connect()
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings").fetchall()}
    finally:
        conn.close()


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn):
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, body.value),
        )
        conn.commit()
        return {"key": key, "value": body.value}
    finally:
        conn.close()
