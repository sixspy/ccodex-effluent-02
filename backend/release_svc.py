"""准排放裁定放行（effluent-02：超标未清禁准排，甲路径为主、乙式影子意图不混表）。"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from effluent02 import (
    RULING_LABEL,
    audit_blocked,
    has_open_hold,
    open_hold_count,
    record_contention,
    request_wants_deviation,
)
from locks import release, require_lock
from models import ReleaseIn, ReleaseIntentIn
from validators import validate_sample_releasable

router = APIRouter(tags=["release"])

OPEN_HOLD_MESSAGE = (
    "仍有开放超标挂起，禁止准排（裁决：超标未清禁准排 · effluent-02 甲路径）"
)


@router.get("/api/releases")
def list_releases(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT r.*, s.sample_code, o.outfall_code FROM discharge_releases r
               JOIN sample_batches s ON s.id=r.sample_id
               JOIN outfalls o ON o.id=r.outfall_id
               ORDER BY r.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/releases")
def create_release(body: ReleaseIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"sample:{body.sample_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            sample = conn.execute(
                "SELECT * FROM sample_batches WHERE id=?", (body.sample_id,)
            ).fetchone()
            if not sample:
                raise HTTPException(404, "采样批次不存在")

            deviation = request_wants_deviation(body)
            open_count = open_hold_count(conn, body.sample_id)

            # 第一道闸必须是“超标未清禁准排”，先于通用状态机，
            # 否则旁路挂起批只会得到“状态不可准排”，超标禁准裁决被吞掉。
            if open_count > 0:
                audit_blocked(
                    conn,
                    user,
                    sample["sample_code"],
                    body.sample_id,
                    deviation=deviation,
                    extra={"open_hold_count": open_count, "via": "direct_release"},
                )
                if deviation:
                    raise HTTPException(
                        400,
                        f"超标禁准偏航：{RULING_LABEL}，任何 force/override 绕闸强准排一律拒写",
                    )
                raise HTTPException(400, OPEN_HOLD_MESSAGE)

            # 同批已有正式准排行：并发/重复请求的败方，只留争用痕，不增行。
            existing = conn.execute(
                "SELECT id, release_ref FROM discharge_releases "
                "WHERE sample_id=? AND status='已准排'",
                (body.sample_id,),
            ).fetchone()
            if existing:
                record_contention(
                    conn,
                    lock_key,
                    user,
                    f"同批正式准排行已存在 {existing['release_ref']}，败方零增",
                )
                raise HTTPException(409, "已准排，勿重复（准排锁争用败方，正式册零增行）")

            validate_sample_releasable(sample["status"])

            ref = f"RL-{sample['sample_code']}"
            try:
                conn.execute(
                    """INSERT INTO discharge_releases(sample_id,outfall_id,release_ref,status,officer,released_at)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        body.sample_id,
                        sample["outfall_id"],
                        ref,
                        "已准排",
                        body.officer,
                        now_iso(),
                    ),
                )
            except sqlite3.IntegrityError:
                # 唯一账实索引兜底：极端并发下另一会话已落正式行。
                record_contention(
                    conn, lock_key, user, "并发落正式行触发唯一账实约束，按败方拒写"
                )
                raise HTTPException(
                    409, "同批准排并发冲突：他会话已准排，败方正式册零增"
                )
            conn.execute(
                "UPDATE sample_batches SET status=? WHERE id=?",
                ("已准排", body.sample_id),
            )
            write_audit(
                conn,
                user,
                "release",
                sample["sample_code"],
                {
                    "release_ref": ref,
                    "officer": body.officer,
                    "ruling": RULING_LABEL,
                },
            )
            conn.commit()
            return {"release_ref": ref, "status": "已准排"}
        finally:
            release(conn, lock_key, user)
            conn.commit()


# ---------------------------------------------------------------------------
# 乙路径（兼容面）：影子准排意图。草稿绝不进 discharge_releases，
# 对账因此不可能把“未升正意图”计成已准排；升正与正式行在同一事务原子完成。
# ---------------------------------------------------------------------------


@router.get("/api/release-intents")
def list_intents(authorization: AuthHeader = None, sample_id: int | None = None):
    require_user(authorization)
    with connect() as conn:
        if sample_id is None:
            rows = conn.execute(
                "SELECT * FROM release_intents ORDER BY id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM release_intents WHERE sample_id=? ORDER BY id DESC",
                (sample_id,),
            ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/release-intents")
@router.post("/api/release_intents")
@router.post("/api/releases/intent")
@router.post("/api/releases/draft")
def create_intent(body: ReleaseIntentIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        sample = conn.execute(
            "SELECT sample_code FROM sample_batches WHERE id=?", (body.sample_id,)
        ).fetchone()
        if not sample:
            raise HTTPException(404, "采样批次不存在")
        n = conn.execute(
            "SELECT COUNT(*) c FROM release_intents WHERE sample_id=?",
            (body.sample_id,),
        ).fetchone()["c"]
        intent_ref = f"INT-{sample['sample_code']}-{int(n) + 1:02d}"
        cur = conn.execute(
            """INSERT INTO release_intents(sample_id,intent_ref,officer,status,note,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                body.sample_id,
                intent_ref,
                body.officer,
                "影子草稿",
                body.note or "",
                now_iso(),
                now_iso(),
            ),
        )
        write_audit(
            conn,
            user,
            "release_intent_draft",
            sample["sample_code"],
            {"intent_ref": intent_ref, "status": "影子草稿"},
        )
        conn.commit()
        return {"id": cur.lastrowid, "intent_ref": intent_ref, "status": "影子草稿"}


@router.post("/api/release-intents/{intent_id}/promote")
@router.post("/api/release_intents/{intent_id}/promote")
def promote_intent(intent_id: int, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        intent = conn.execute(
            "SELECT * FROM release_intents WHERE id=?", (intent_id,)
        ).fetchone()
        if not intent:
            raise HTTPException(404, "准排意图不存在")
        sample_id = int(intent["sample_id"])
        if intent["status"] == "已升正" and intent["promoted_release_id"]:
            rel = conn.execute(
                "SELECT release_ref FROM discharge_releases WHERE id=?",
                (intent["promoted_release_id"],),
            ).fetchone()
            return {
                "id": intent_id,
                "status": "已升正",
                "release_ref": rel["release_ref"] if rel else None,
                "idempotent": True,
            }

        lock_key = f"sample:{sample_id}"
        require_lock(conn, lock_key, user)
        try:
            sample = conn.execute(
                "SELECT * FROM sample_batches WHERE id=?", (sample_id,)
            ).fetchone()
            if not sample:
                raise HTTPException(404, "采样批次不存在")

            # 同一道超标禁准闸：开放挂起仍在 → 草稿作废、正式册零改。
            if has_open_hold(conn, sample_id):
                conn.execute(
                    "UPDATE release_intents SET status='已作废', updated_at=? WHERE id=?",
                    (now_iso(), intent_id),
                )
                audit_blocked(
                    conn,
                    user,
                    sample["sample_code"],
                    sample_id,
                    deviation=False,
                    extra={"intent_id": intent_id, "via": "promote_intent"},
                )
                raise HTTPException(400, OPEN_HOLD_MESSAGE + "；影子意图已作废")

            existing = conn.execute(
                "SELECT id, release_ref FROM discharge_releases "
                "WHERE sample_id=? AND status='已准排'",
                (sample_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE release_intents SET status='已升正',
                       promoted_release_id=?, updated_at=? WHERE id=?""",
                    (existing["id"], now_iso(), intent_id),
                )
                conn.commit()
                return {
                    "id": intent_id,
                    "status": "已升正",
                    "release_ref": existing["release_ref"],
                    "idempotent": True,
                }

            validate_sample_releasable(sample["status"])
            ref = f"RL-{sample['sample_code']}"
            cur = conn.execute(
                """INSERT INTO discharge_releases(sample_id,outfall_id,release_ref,status,officer,released_at)
                   VALUES (?,?,?,?,?,?)""",
                (sample_id, sample["outfall_id"], ref, "已准排", intent["officer"], now_iso()),
            )
            conn.execute(
                "UPDATE sample_batches SET status='已准排' WHERE id=?", (sample_id,)
            )
            conn.execute(
                """UPDATE release_intents SET status='已升正',
                   promoted_release_id=?, updated_at=? WHERE id=?""",
                (cur.lastrowid, now_iso(), intent_id),
            )
            write_audit(
                conn,
                user,
                "release_intent_promote",
                sample["sample_code"],
                {"intent_id": intent_id, "release_ref": ref, "atomic": True},
            )
            conn.commit()
            return {"id": intent_id, "status": "已升正", "release_ref": ref}
        finally:
            release(conn, lock_key, user)
            conn.commit()
