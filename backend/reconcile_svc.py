"""排放许可与采样对账证明。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from effluent02 import rectify_open_hold_releases, release_truth
from validators import validate_permit_match

router = APIRouter(tags=["reconcile"])


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT permit_code FROM discharge_permits ORDER BY permit_code"
        ).fetchall()
        return {"items": [_reconcile_permit(conn, r["permit_code"]) for r in rows]}


@router.get("/api/reconcile/{permit_code}")
def reconcile_one(permit_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        if not conn.execute(
            "SELECT id FROM discharge_permits WHERE permit_code=?", (permit_code,)
        ).fetchone():
            raise HTTPException(404, "许可不存在")
        return _reconcile_permit(conn, permit_code)


def _reconcile_permit(conn, permit_code: str) -> dict:
    permit = conn.execute(
        "SELECT * FROM discharge_permits WHERE permit_code=?", (permit_code,)
    ).fetchone()
    samples = conn.execute(
        """SELECT id, sample_code, flow_m3h, metrics, status, generation FROM sample_batches
           WHERE permit_ref=? AND status NOT IN ('作废')""",
        (permit_code,),
    ).fetchall()
    if not samples:
        return {
            "permit_code": permit_code,
            "balanced": False,
            "reason": "无采样批次",
            "samples": [],
            "released_count": 0,
            "release_count": 0,
            "valid_released_count": 0,
            "open_hold_count": 0,
            "release_blocked_count": 0,
            "mixed_ledger_count": 0,
            "release_complete": True,
            "complete_set": True,
            "complete_lamp": "绿",
            "lamp": "绿",
        }
    caps = {
        "cod": permit["cod_cap"],
        "nh3n": permit["nh3n_cap"],
        "tp": permit["tp_cap"],
    }
    details = []
    all_ok = True
    released_count = 0
    open_hold_samples = 0
    blocked_count = 0
    mixed_count = 0
    for s in samples:
        metrics = json.loads(s["metrics"] or "{}")
        ok = validate_permit_match(
            caps, metrics, float(s["flow_m3h"]), float(permit["quota_m3d"])
        )
        if not ok:
            all_ok = False
        truth = release_truth(conn, int(s["id"]))
        if truth["release_ready"]:
            released_count += 1
        if truth["has_open_hold"]:
            open_hold_samples += 1
            blocked_count += 1
        if not truth["ledger_aligned"]:
            mixed_count += 1
        details.append(
            {
                "sample_code": s["sample_code"],
                "status": s["status"],
                "generation": s["generation"],
                "metrics": metrics,
                "flow_m3h": s["flow_m3h"],
                "matched": ok,
                # effluent-02 账实准排口径：影子意图/未闭环硬准排不计已准排。
                **truth,
            }
        )
    seal_rows = conn.execute(
        """SELECT d.seal_ref, d.sample_generation, s.sample_code
           FROM discharge_seals d JOIN sample_batches s ON s.id=d.sample_id
           WHERE s.permit_ref=?""",
        (permit_code,),
    ).fetchall()
    release_complete = blocked_count == 0 and mixed_count == 0
    return {
        "permit_code": permit_code,
        "quota_m3d": permit["quota_m3d"],
        "caps": caps,
        "balanced": all_ok,
        "proof_ready": all_ok and len(seal_rows) >= 1,
        "seal_count": len(seal_rows),
        # effluent-02 对账齐套灯：开放超标/混用不清不得报绿；
        # released_count 只数“有正式行且无开放挂起”的有效已准排。
        "released_count": released_count,
        "release_count": released_count,
        "valid_released_count": released_count,
        "open_hold_count": open_hold_samples,
        "release_blocked_count": blocked_count,
        "mixed_ledger_count": mixed_count,
        "release_complete": release_complete,
        "complete_set": release_complete,
        "complete_lamp": "绿" if (all_ok and release_complete) else "红",
        "lamp": "绿" if (all_ok and release_complete) else "红",
        "samples": details,
        "seals": [dict(x) for x in seal_rows],
    }


@router.get("/api/proof/sample/{sample_code}")
def proof_sample(sample_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        sample = conn.execute(
            "SELECT * FROM sample_batches WHERE sample_code=?", (sample_code,)
        ).fetchone()
        if not sample:
            raise HTTPException(404, "采样不存在")
        holds = conn.execute(
            "SELECT * FROM exceed_holds WHERE sample_id=?", (sample["id"],)
        ).fetchall()
        releases = conn.execute(
            "SELECT * FROM discharge_releases WHERE sample_id=?", (sample["id"],)
        ).fetchall()
        seals = conn.execute(
            "SELECT * FROM discharge_seals WHERE sample_id=?", (sample["id"],)
        ).fetchall()
        open_holds = [h for h in holds if h["status"] in ("开放", "开放挂起")]
        closed = [h for h in holds if h["status"] in ("已关闭", "已闭环")]
        has_formal_release = any(r["status"] == "已准排" for r in releases)
        # effluent-02：released 是账实有效口径——开放超标未闭环时，
        # 即便存在硬落的正式准排行也不得判为已准排（原始行仍在 releases 可见）。
        valid_released = has_formal_release and len(open_holds) == 0
        return {
            "sample_code": sample_code,
            "status": sample["status"],
            "generation": sample["generation"],
            "released": valid_released,
            "has_formal_release": has_formal_release,
            "formal_release_rows": sum(
                1 for r in releases if r["status"] == "已准排"
            ),
            "open_hold_count": len(open_holds),
            "release_blocked": len(open_holds) > 0,
            "release_ready": valid_released,
            "sealed": any(s["status"] == "已封批" for s in seals),
            "exceed_closed": len(open_holds) == 0 and (len(closed) > 0 or sample["status"] != "旁路挂起"),
            "holds": [dict(h) for h in holds],
            "releases": [dict(r) for r in releases],
            "seals": [dict(s) for s in seals],
        }


@router.post("/api/reconcile/rectify-open-hold-releases")
def rectify_open_hold_releases_api(authorization: AuthHeader = None):
    """同事务核销“开放挂起 × 硬准排”混用脏状态（清掉开放挂起上的假准排）。"""
    user = require_writer(authorization)
    from db import connect

    with connect() as conn:
        report = rectify_open_hold_releases(conn, actor=user)
        conn.commit()
        return {"ok": True, **report}
