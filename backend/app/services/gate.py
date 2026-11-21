from __future__ import annotations
from fastapi import HTTPException

from app.db import connect

# 分类型架构：合法表类型以此为准，封印/对齐缺类型或带错类型一律拒绝
METER_TYPES = ("water", "electric")
_TYPE_LABEL = {"water": "水表", "electric": "电表"}


def normalize_meter_type(meter_type: str | None) -> str:
    """封印与对齐入口的强制类型校验：无类型不得宣称分类型封。"""
    if not meter_type:
        raise HTTPException(422, "缺少表类型参数 meter_type（water/electric），封印与对齐必须按表类型分桶")
    if meter_type not in METER_TYPES:
        raise HTTPException(400, f"非法表类型: {meter_type}（仅支持 water/electric）")
    return meter_type


def type_label(meter_type: str) -> str:
    return _TYPE_LABEL.get(meter_type, meter_type)


def ensure_gates(conn, period_id: str) -> None:
    """确保账期下每类表各有一条写闸；账期不存在则 404。"""
    period = conn.execute(
        "SELECT 1 FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if not period:
        raise HTTPException(404, f"账期不存在: {period_id}")
    for meter_type in METER_TYPES:
        conn.execute(
            "INSERT OR IGNORE INTO period_gates(period_id,meter_type,status) VALUES (?,?,'open')",
            (period_id, meter_type),
        )


def meter_type_of(conn, meter_id: str) -> str:
    row = conn.execute(
        "SELECT meter_type FROM meters WHERE meter_id=?", (meter_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "表具不存在")
    return row["meter_type"]


def gate_status(conn, period_id: str, meter_type: str) -> str:
    """(period_id, meter_type) 这一条写闸的权威状态；缺失视为 open。"""
    row = conn.execute(
        "SELECT status FROM period_gates WHERE period_id=? AND meter_type=?",
        (period_id, meter_type),
    ).fetchone()
    return row["status"] if row else "open"


def assert_type_writable(conn, period_id: str, meter_type: str) -> None:
    """只校验该类型自己的写闸：水表封不影响电表，反之亦然。

    写闸状态只以 period_gates 为准，不再读取任何旁路缓存/一次性 paint，
    避免"闸与回执各说各话"。
    """
    ensure_gates(conn, period_id)
    if gate_status(conn, period_id, meter_type) == "sealed":
        raise HTTPException(
            409,
            f"账期 {period_id} 的{type_label(meter_type)}已封印，禁止写入；"
            f"另一表类型若未封印仍可写",
        )


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()
