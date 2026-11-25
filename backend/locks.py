"""排放批争用锁。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from effluent02 import record_contention

router = APIRouter(tags=["locks"])


def require_lock(conn, lock_key: str, holder: str) -> None:
    """占锁；他会话持有则落“争用失败”痕并 409。

    effluent-02：争用痕必须在抛错前显式提交——本函数在各服务里都位于
    try/finally 之前，若只 INSERT 后直接 raise，外层 with connect() 会
    rollback，争用痕就丢了（败方“无痕迹”的根因）。同时兜住并发
    check-then-insert 的主键竞争，避免 500。
    """
    row = conn.execute(
        "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
    ).fetchone()
    if row and row["holder"] != holder:
        record_contention(conn, lock_key, holder, f"持有人={row['holder']}")
        conn.commit()
        raise HTTPException(409, f"锁被占用：{lock_key} 持有人 {row['holder']}")
    if row:
        # 同持有人可重入（同一会话/用户的连续动作）。
        return
    try:
        conn.execute(
            "INSERT INTO effluent_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
            (lock_key, holder, now_iso()),
        )
    except sqlite3.IntegrityError:
        raced = conn.execute(
            "SELECT holder FROM effluent_locks WHERE lock_key=?", (lock_key,)
        ).fetchone()
        if raced and raced["holder"] != holder:
            record_contention(conn, lock_key, holder, f"并发占锁失败，持有人={raced['holder']}")
            conn.commit()
            raise HTTPException(
                409, f"锁被占用：{lock_key} 持有人 {raced['holder']}"
            )
        # 同行已由本持有人插入：按重入处理。


def release(conn, lock_key: str, holder: str) -> None:
    conn.execute(
        "DELETE FROM effluent_locks WHERE lock_key=? AND holder=?",
        (lock_key, holder),
    )


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        locks = [dict(r) for r in conn.execute("SELECT * FROM effluent_locks").fetchall()]
        logs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contention_log ORDER BY id DESC LIMIT 50"
            ).fetchall()
        ]
        return {"locks": locks, "contention": logs}


@router.post("/api/locks/acquire")
def acquire(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        require_lock(conn, lock_key, user)
        write_audit(conn, user, "lock_acquire", lock_key, {})
        conn.commit()
        return {"ok": True, "lock_key": lock_key, "holder": user}


@router.post("/api/locks/release")
def release_api(lock_key: str, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, lock_key, user)
        write_audit(conn, user, "lock_release", lock_key, {})
        conn.commit()
        return {"ok": True}
