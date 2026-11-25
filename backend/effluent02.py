# -*- coding: utf-8 -*-
"""effluent-02 域口径：开放超标 × 准排写口不得混用。

已封取舍（甲路径，2026-11-25 封存，见 docs/effluent-02-超标禁准裁决.md）：
开放挂起仍在 → 准排写口正式行零增并拒写；拒绝也要留下“超标禁准”裁决痕。
乙式影子意图（release_intents）仅作“未升正”登记，原子升正时必须过同一道
超标禁准闸；未升正/已作废意图一律不得被对账计为已准排。

本模块只放无副作用的口径与同事务核销助手，供 release/exceed/samples/
reconcile 四个服务共用，避免再长“单点挂起校验”和各页口径漂移。
"""
from __future__ import annotations

import sqlite3

from db import now_iso, write_audit

# 超标挂起册里一切表示“未闭环”的状态。种子标准值为“开放”，
# “开放挂起”是同一未闭环态的全称写法，查询一并认。
OPEN_HOLD_STATUSES: tuple[str, ...] = ("开放", "开放挂起")
CLOSED_HOLD_STATUSES: tuple[str, ...] = ("已关闭", "已闭环", "关闭", "闭环")

# 裁决/留痕用的稳定记号（中文动作名直接落 audit_trail.action）。
RULING_CODE = "EXCEED_OPEN_RELEASE_FORBIDDEN"
RULING_LABEL = "超标未清禁准排"
# 审计 action 直接用裁决原词，保证“超标禁准裁决写入说明/留痕”可被检索；
# 英文事件名放 payload.event 供工具识别。
BLOCK_AUDIT_ACTION = "超标禁准"
BLOCK_EVENT = "release_blocked_exceed"
PIANHANG_ACTION = "超标禁准偏航"
CONTENTION_ACTION = "争用失败"

# 正式准排册的有效状态；脏的硬准排被核销后改挂此状态，退出唯一索引。
VOID_RELEASE_STATUS = "已作废（超标禁准）"

# 强准排/绕闸字段：任何一个为真都算“偏离已封取舍”。
_DEVIATION_KEYS = (
    "force",
    "override",
    "bypass_exceed",
    "bypass",
    "ignore_open_hold",
    "ignore_hold",
    "approve_despite_exceed",
    "despite_exceed",
    "破例",
    "强准排",
)


def open_status_predicate(alias: str = "h") -> str:
    """返回“该挂起行处于开放未闭环态”的 SQL 片段（参数用 ? 绑定）。"""
    placeholders = ",".join("?" for _ in OPEN_HOLD_STATUSES)
    return f"{alias}.status IN ({placeholders})"


def open_hold_count(conn: sqlite3.Connection, sample_id: int) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) c FROM exceed_holds h WHERE h.sample_id=? "
        f"AND {open_status_predicate('h')}",
        (sample_id, *OPEN_HOLD_STATUSES),
    ).fetchone()
    return int(row["c"]) if row else 0


def has_open_hold(conn: sqlite3.Connection, sample_id: int) -> bool:
    return open_hold_count(conn, sample_id) > 0


def formal_release_count(conn: sqlite3.Connection, sample_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM discharge_releases WHERE sample_id=? AND status='已准排'",
        (sample_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def release_truth(conn: sqlite3.Connection, sample_id: int) -> dict:
    """账实口径的准排真值：有效已准排 = 有正式已准排行 且 无开放超标挂起。

    原始正式行数另行返回（脏行可见、可核销），避免把“见过硬行”误读成有效准排。
    """
    open_holds = open_hold_count(conn, sample_id)
    formal = formal_release_count(conn, sample_id)
    valid = formal >= 1 and open_holds == 0
    return {
        "open_hold_count": open_holds,
        "has_open_hold": open_holds > 0,
        "formal_release_rows": formal,
        "has_formal_release": formal >= 1,
        "release_blocked": open_holds > 0,
        "release_ready": valid,
        "released": valid,
        "valid_released": valid,
        "released_valid": valid,
        # 跨表账实：开放挂起与有效正式准排不得并存。
        "ledger_aligned": not (open_holds > 0 and formal > 0),
    }


def request_wants_deviation(payload) -> bool:
    """识别 force/override/绕闸等“第三写法/偏航”企图。"""
    if payload is None:
        return False
    if isinstance(payload, dict):
        data = payload
    else:
        # pydantic 模型：显式字段 + extra='allow' 收到的额外字段
        data = {}
        for key in _DEVIATION_KEYS:
            try:
                val = getattr(payload, key, False)
            except Exception:  # noqa: BLE001
                val = False
            if val:
                return True
        extra = getattr(payload, "model_extra", None) or {}
        data = extra
    for key, val in data.items():
        kl = str(key).lower()
        if val and (
            key in _DEVIATION_KEYS
            or any(tok in kl for tok in ("force", "override", "bypass", "ignore", "despite"))
        ):
            return True
    return False


def audit_blocked(conn, user: str, sample_code: str, sample_id: int, *, deviation: bool, extra: dict | None = None) -> None:
    """把一次拒写/偏航写进审计（取舍痕），与 contention_log 争用痕分写。"""
    payload = {
        "event": BLOCK_EVENT if not deviation else PIANHANG_ACTION,
        "code": RULING_CODE,
        "ruling": RULING_LABEL,
        "sample_id": sample_id,
        "deviation": bool(deviation),
    }
    if extra:
        payload.update(extra)
    action = PIANHANG_ACTION if deviation else BLOCK_AUDIT_ACTION
    write_audit(conn, user, action, sample_code, payload)


def record_contention(conn, lock_key: str, actor: str, detail: str) -> None:
    """只写争用痕（不提交；由调用方事务/finally 统一提交）。"""
    conn.execute(
        """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
           VALUES (?,?,?,?,?)""",
        (lock_key, actor, CONTENTION_ACTION, detail, now_iso()),
    )


def rectify_open_hold_releases(conn: sqlite3.Connection, actor: str = "effluent-02") -> dict:
    """同事务核销“开放挂起 × 硬准排”混用脏状态（清掉开放挂起上的假准排）。

    - 把与开放挂起并存的有效正式准排行改挂 VOID_RELEASE_STATUS（退出唯一索引）；
    - 批态从“已准排/已封批”拉回“旁路挂起”；
    - 每张偏航行写一条“超标禁准偏航”审计。
    调用方负责 commit；本函数不开闭事务，便于与其它账实修改同事务提交。
    """
    rows = conn.execute(
        f"""SELECT r.id rid, r.sample_id sid, s.sample_code code
            FROM discharge_releases r
            JOIN sample_batches s ON s.id = r.sample_id
            WHERE r.status='已准排'
              AND EXISTS (
                SELECT 1 FROM exceed_holds h WHERE h.sample_id = r.sample_id
                  AND {open_status_predicate('h')}
              )""",
        (*OPEN_HOLD_STATUSES,),
    ).fetchall()
    voided = []
    for row in rows:
        conn.execute(
            "UPDATE discharge_releases SET status=? WHERE id=?",
            (VOID_RELEASE_STATUS, row["rid"]),
        )
        conn.execute(
            "UPDATE sample_batches SET status='旁路挂起' WHERE id=? AND status!='作废'",
            (row["sid"],),
        )
        write_audit(
            conn,
            actor,
            PIANHANG_ACTION,
            row["code"],
            {"release_row_id": row["rid"], "reason": RULING_LABEL, "rectified": True},
        )
        voided.append({"release_row_id": row["rid"], "sample_code": row["code"]})
    return {"voided": voided, "voided_count": len(voided)}
