# -*- coding: utf-8 -*-
"""effluent-02 · 失败测甲：开放超标未闭环，准排写口正式行零增并拒写。

事故夹具 SM-B17：超标挂起册仍有“开放”行，准排正式册却已被硬落了一张
“已准排”行。甲路径口径——开放挂起仍在，则准排写口正式行零增并拒写；
“超标禁准”裁决必须留痕，任何强准排/偏航尝试记“超标禁准偏航”并拒写。
"""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app import app
from db import connect
from effluent02_lib import (
    B17,
    audit_actions,
    create_exceeded_batch,
    formal_release_count,
    login_headers,
    seed_b17_accident,
    use_tmp_db,
)


def _client(tmp_path):
    use_tmp_db(tmp_path)
    return TestClient(app)


def test_jia_accident_fixture_formal_ledger_zero_growth_on_rewrite(tmp_path):
    """开放挂起仍在：对 SM-B17 再次硬准排必须拒写，正式准排册零增。"""
    c = _client(tmp_path)
    h = login_headers(c)
    sid = seed_b17_accident()

    before = formal_release_count(sid)
    assert before == 1  # 事故夹具里那张硬准排行

    r = c.post(
        "/api/releases",
        headers=h,
        json={"sample_id": sid, "officer": "甲路径复测"},
    )
    assert r.status_code in (400, 409), r.text
    detail = r.json().get("detail", "")
    assert "超标" in detail, f"拒写理由必须是超标禁准裁决，实际：{detail!r}"

    after = formal_release_count(sid)
    assert after == before == 1, (
        f"开放超标未闭环，正式准排册不得增行：before={before} after={after}"
    )


def test_jia_natural_open_hold_batch_blocked_by_exceed_verdict_not_status(tmp_path):
    """正常入库超标的旁路挂起批，准排也必须吃到“超标禁准”裁决。"""
    c = _client(tmp_path)
    h = login_headers(c)
    sid = create_exceeded_batch(c, h, "SM-JIA-OPEN")

    assert formal_release_count(sid) == 0
    r = c.post(
        "/api/releases", headers=h, json={"sample_id": sid, "officer": "甲"}
    )
    assert r.status_code in (400, 409), r.text
    assert "超标" in r.json()["detail"]
    assert formal_release_count(sid) == 0

    acts = audit_actions(c, h)
    assert any(
        "超标禁准" in (a.get("action") or "")
        or "超标禁准" in (a.get("payload") or "")
        for a in acts
    ), "拒写必须留下‘超标禁准’裁决/审计痕"


def test_jia_force_override_is_pianhang_and_rejected(tmp_path):
    """带 force/override 的硬准排 = 偏离已封取舍，记‘超标禁准偏航’并拒写。"""
    c = _client(tmp_path)
    h = login_headers(c)
    sid = seed_b17_accident(code="SM-JIA-FORCE")

    for extra in (
        {"force": True},
        {"override": True},
        {"bypass_exceed": True},
    ):
        before = formal_release_count(sid)
        r = c.post(
            "/api/releases",
            headers=h,
            json={"sample_id": sid, "officer": "硬来", **extra},
        )
        assert r.status_code in (400, 403, 409), r.text
        assert formal_release_count(sid) == before

    acts = audit_actions(c, h)
    assert any(
        "超标禁准偏航" in (a.get("action") or "")
        or "超标禁准偏航" in (a.get("payload") or "")
        for a in acts
    ), "强准排偏航必须记为‘超标禁准偏航’"


def test_jia_formal_ledger_storage_invariant_no_second_valid_row(tmp_path):
    """跨表闸的存储兜底：同一批至多一张有效正式准排行（账实唯一）。

    种子库没有这层唯一约束时，下面的第二次 INSERT 会成功——即
    “正式准排册仍能增行”，本测试见红；补唯一账实索引后转绿。
    """
    use_tmp_db(tmp_path)
    sid = seed_b17_accident(code="SM-JIA-UQ")

    blocked_by_constraint = False
    with connect() as conn:
        row = conn.execute(
            "SELECT outfall_id FROM sample_batches WHERE id=?", (sid,)
        ).fetchone()
        try:
            conn.execute(
                """INSERT INTO discharge_releases(sample_id,outfall_id,release_ref,status,
                   officer,released_at) VALUES (?,?,?,?,?,?)""",
                (sid, row["outfall_id"], "RL-JIA-DUP", "已准排", "重复行", "2026-11-25T00:00:00"),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            blocked_by_constraint = True

    n = formal_release_count(sid)
    assert blocked_by_constraint or n == 1, (
        f"正式准排册仍能增行：SM-JIA-UQ 现有 {n} 张有效正式准排行且无唯一约束兜底"
    )
    assert n <= 1
