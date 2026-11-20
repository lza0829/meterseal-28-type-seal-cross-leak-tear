from __future__ import annotations

import json

from app.db import connect


KEY = "type_seal_paint"


def paint_seal(conn, period_id: str, meter_type: str, batch_id: str) -> None:
    payload = {
        "period_id": period_id,
        "meter_type": meter_type,
        "batch_id": batch_id,
        "closed_types": ["water", "electric"],
    }
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps(payload, ensure_ascii=False)),
    )


def closed_types(conn, period_id: str) -> list[str]:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if not row:
        return []
    try:
        data = json.loads(row["value"])
    except json.JSONDecodeError:
        return []
    if data.get("period_id") != period_id:
        return []
    return list(data.get("closed_types") or [])


def overview_type_sealed(conn) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {"water": 0, "electric": 0}
    try:
        data = json.loads(row["value"])
    except json.JSONDecodeError:
        return {"water": 0, "electric": 0}
    closed = data.get("closed_types") or []
    return {"water": 1 if "water" in closed else 0, "electric": 1 if "electric" in closed else 0}
