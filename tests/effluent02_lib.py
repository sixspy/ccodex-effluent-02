# -*- coding: utf-8 -*-
"""effluent-02 共用事故夹具与计数工具（测试侧）。

事故原型：采样 SM-B17 仍挂着“开放”超标挂起册行，准排写口却已经硬落了
一张 status='已准排' 的正式准排行，采样批态被翻成“已准排”，对账/证明
据此把它读成“已准排”。这是“开放超标”与“准排写口”混用的脏状态。
"""
from __future__ import annotations

from pathlib import Path

import db
from db import connect, init_db, now_iso

ROOT = Path(__file__).resolve().parents[1]

PERMIT = "DP-2026-E01"
OUTFALL_CODE = "OF-E1-A"
B17 = "SM-B17"


def use_tmp_db(tmp_path) -> Path:
    """每个测试一个独立文件库（并发线程共享同一文件）。"""
    path = Path(tmp_path) / "effluent_02.db"
    db.DB_PATH = path
    init_db()
    return path


def login_headers(client, username: str = "admin", password: str = "admin123") -> dict:
    token = client.post(
        "/api/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + token}


def _outfall_id(conn) -> int:
    return conn.execute(
        "SELECT id FROM outfalls WHERE outfall_code=?", (OUTFALL_CODE,)
    ).fetchone()["id"]


def seed_b17_accident(
    *,
    open_status: str = "开放",
    sample_status: str = "已准排",
    hard_row: bool = True,
    code: str = B17,
    measured: float = 88.0,
) -> int:
    """种下“开放超标 + 硬准排正式行”的事故夹具，返回 sample_id。"""
    with connect() as conn:
        outfall_id = _outfall_id(conn)
        existed = conn.execute(
            "SELECT id FROM sample_batches WHERE sample_code=?", (code,)
        ).fetchone()
        if existed:
            sid = existed["id"]
        else:
            cur = conn.execute(
                """INSERT INTO sample_batches(sample_code,outfall_id,permit_ref,metrics,
                   flow_m3h,generation,status,adjudicated_at,created_at)
                   VALUES (?,?,?,?,?,1,?,?,?)""",
                (
                    code,
                    outfall_id,
                    PERMIT,
                    '{"cod": %.1f, "nh3n": 8.8, "tp": 0.88}' % measured,
                    120.0,
                    sample_status,
                    now_iso() if sample_status != "旁路挂起" else None,
                    now_iso(),
                ),
            )
            sid = cur.lastrowid
        conn.execute(
            """INSERT INTO exceed_holds(sample_id,hold_key,exceed_metric,measured,
               limit_value,status,opened_at,closed_at,note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                sid,
                f"EX-{code}-COD-E02",
                "COD",
                measured,
                50.0,
                open_status,
                now_iso(),
                None,
                "effluent-02 事故夹具：开放超标未闭环",
            ),
        )
        if hard_row:
            conn.execute(
                """INSERT INTO discharge_releases(sample_id,outfall_id,release_ref,status,
                   officer,released_at) VALUES (?,?,?,?,?,?)""",
                (
                    sid,
                    outfall_id,
                    f"RL-{code}-HARD",
                    "已准排",
                    "硬准排（事故）",
                    now_iso(),
                ),
            )
        conn.commit()
        return int(sid)


def create_exceeded_batch(client, headers, code: str = "SM-E02-OPEN") -> int:
    """走真实采样入库口造一个“旁路挂起 + 开放挂起”的批。"""
    outfalls = client.get("/api/outfalls", headers=headers).json()["items"]
    of = next(x for x in outfalls if x["outfall_code"] == OUTFALL_CODE)
    r = client.post(
        "/api/samples",
        headers=headers,
        json={
            "outfall_id": of["id"],
            "permit_ref": PERMIT,
            "sample_code": code,
            "flow_m3h": 120,
            "metrics": {"cod": 77.0, "nh3n": 7.7, "tp": 0.77},
        },
    )
    assert r.status_code == 200, r.text
    return int(r.json()["id"])


def create_clean_batch(client, headers, code: str) -> int:
    outfalls = client.get("/api/outfalls", headers=headers).json()["items"]
    of = next(x for x in outfalls if x["outfall_code"] == OUTFALL_CODE)
    r = client.post(
        "/api/samples",
        headers=headers,
        json={
            "outfall_id": of["id"],
            "permit_ref": PERMIT,
            "sample_code": code,
            "flow_m3h": 120,
            "metrics": {"cod": 20.0, "nh3n": 1.5, "tp": 0.12},
        },
    )
    assert r.status_code == 200, r.text
    return int(r.json()["id"])


def formal_release_rows(sample_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, release_ref, status, officer FROM discharge_releases
               WHERE sample_id=? ORDER BY id""",
            (sample_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def formal_release_count(sample_id: int) -> int:
    return sum(1 for r in formal_release_rows(sample_id) if r["status"] == "已准排")


def contention_for(lock_key: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=? ORDER BY id", (lock_key,)
        ).fetchall()
        return [dict(r) for r in rows]


def audit_actions(client, headers) -> list[dict]:
    r = client.get("/api/audit?limit=500", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]
