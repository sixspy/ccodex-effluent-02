# -*- coding: utf-8 -*-
"""effluent-02 · 失败测乙：对账不得把“未升正意图/开放挂起上的硬准排”记成已准排。

事故夹具 SM-B17 同时有开放超标挂起与一张硬落的正式准排行。对账“齐套灯”
与采样证明的 released 必须按“正式准排行 + 无开放超标”的账实口径计算，
不能只读 sample_batches.status='已准排' 或见正式行就算已准排。

若走乙路径（影子草稿），未原子升正的意图一律不得计入已准排；开放挂起批
升正必须失败且草稿作废、正式册零改。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import app
from effluent02_lib import (
    PERMIT,
    create_clean_batch,
    formal_release_count,
    login_headers,
    seed_b17_accident,
    use_tmp_db,
)


def _client(tmp_path):
    use_tmp_db(tmp_path)
    return TestClient(app)


def test_yi_proof_does_not_mark_open_hold_hard_row_released(tmp_path):
    """证明口：开放超标未闭环时，即便有硬准排行也不得判为有效已准排。"""
    c = _client(tmp_path)
    h = login_headers(c)
    seed_b17_accident()

    proof = c.get("/api/proof/sample/SM-B17", headers=h).json()
    assert proof["exceed_closed"] is False
    assert proof.get("release_blocked") is True
    assert proof["released"] is False, (
        "开放挂起未闭环，证明口不得把硬准排脏行读成有效已准排：%r" % proof
    )
    # 原始正式行仍可被看见（账实可追溯），但不算有效准排
    assert proof.get("has_formal_release") is True


def test_yi_reconcile_lamp_excludes_open_hold_fake_release(tmp_path):
    """对账口：B17 明细 release_ready=False，聚合已准排数不含 B17，齐套灯不绿。"""
    c = _client(tmp_path)
    h = login_headers(c)
    seed_b17_accident()

    data = c.get(f"/api/reconcile/{PERMIT}", headers=h).json()
    b17 = next(s for s in data["samples"] if s["sample_code"] == "SM-B17")

    assert b17.get("has_open_hold") is True
    assert b17.get("release_ready") is False, b17
    assert b17.get("formal_release_rows", 0) >= 1  # 脏行看得到
    assert b17.get("open_hold_count", 0) >= 1

    # 聚合口径：有效已准排数不得把 B17 计进去
    assert "released_count" in data, "对账必须给出 released_count 账实聚合"
    codes_released = {
        s["sample_code"] for s in data["samples"] if s.get("release_ready")
    }
    assert "SM-B17" not in codes_released
    assert data["released_count"] == len(codes_released)
    assert data.get("release_blocked_count", 0) >= 1
    # 齐套灯：存在开放超标/混用，不得报绿
    assert data.get("complete_lamp") in ("红", "RED", "red", False) or data.get(
        "release_complete"
    ) is False


def test_yi_shadow_intent_not_counted_until_promoted(tmp_path):
    """乙路径：影子准排意图在原子升正前，对账/证明一律不计已准排。"""
    c = _client(tmp_path)
    h = login_headers(c)
    sid = create_clean_batch(c, h, "SM-YI-DRAFT")

    reg = c.post(
        "/api/release-intents",
        headers=h,
        json={"sample_id": sid, "officer": "乙草稿"},
    )
    assert reg.status_code == 200, reg.text
    intent = reg.json()
    iid = intent.get("id") or intent.get("intent_id")
    assert intent.get("status") in ("影子草稿", "DRAFT", "draft")
    assert formal_release_count(sid) == 0

    proof = c.get("/api/proof/sample/SM-YI-DRAFT", headers=h).json()
    assert proof["released"] is False

    rec = c.get(f"/api/reconcile/{PERMIT}", headers=h).json()
    row = next(s for s in rec["samples"] if s["sample_code"] == "SM-YI-DRAFT")
    assert row.get("release_ready") is False

    # 原子升正后才落唯一正式行并被计为已准排
    prom = c.post(f"/api/release-intents/{iid}/promote", headers=h)
    assert prom.status_code == 200, prom.text
    assert formal_release_count(sid) == 1
    proof2 = c.get("/api/proof/sample/SM-YI-DRAFT", headers=h).json()
    assert proof2["released"] is True


def test_yi_shadow_intent_on_open_hold_is_voided_on_promote(tmp_path):
    """开放挂起批：意图升正被超标禁准闸拒绝，草稿作废且正式册零改。"""
    c = _client(tmp_path)
    h = login_headers(c)
    sid = seed_b17_accident(code="SM-YI-VOID", sample_status="旁路挂起", hard_row=False)

    reg = c.post(
        "/api/release-intents",
        headers=h,
        json={"sample_id": sid, "officer": "乙作废"},
    )
    assert reg.status_code == 200, reg.text
    iid = reg.json().get("id") or reg.json().get("intent_id")

    prom = c.post(f"/api/release-intents/{iid}/promote", headers=h)
    assert prom.status_code in (400, 409), prom.text
    assert "超标" in prom.json()["detail"]
    assert formal_release_count(sid) == 0

    lst = c.get("/api/release-intents", headers=h).json()["items"]
    mine = next(x for x in lst if x["id"] == iid)
    assert mine["status"] in ("已作废", "VOID", "void")
