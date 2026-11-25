# -*- coding: utf-8 -*-
"""effluent-02 · 争用失败测：两路会话对 SM-B17/同批准排同时抢准排锁。

口径：同批准排必须占 `sample:<id>` 准排锁；只允许一个会话落唯一正式行，
败方零行且只留 contention_log 争用痕（不写正式准排、不翻批态）。
另要求并发脚本 scripts/ping_release_lock.py 提供 ping_release_lock 打同批。
"""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from effluent02_lib import (
    contention_for,
    create_clean_batch,
    formal_release_count,
    login_headers,
    use_tmp_db,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ping_release_lock.py"


def _clients(tmp_path):
    use_tmp_db(tmp_path)
    return TestClient(app), TestClient(app)


def _tokens(c: TestClient):
    admin = login_headers(c, "admin", "admin123")
    writer = login_headers(c, "writer", "writer123")
    return admin, writer


def test_zheng_held_release_lock_foreign_session_409_and_trace(tmp_path):
    """他会话正持准排锁时，第二会话准排必须 409 并写争用痕、零正式行。"""
    c, _ = _clients(tmp_path)
    admin, writer = _tokens(c)
    sid = create_clean_batch(c, admin, "SM-ZHENG-HELD")
    key = f"sample:{sid}"

    acq = c.post(f"/api/locks/acquire?lock_key={key}", headers=admin)
    assert acq.status_code == 200, acq.text

    r = c.post(
        "/api/releases", headers=writer, json={"sample_id": sid, "officer": "writer"}
    )
    assert r.status_code == 409, r.text

    logs = contention_for(key)
    assert any(x.get("actor") == "writer" and x.get("action") == "争用失败" for x in logs), logs
    assert formal_release_count(sid) == 0

    c.post(f"/api/locks/release?lock_key={key}", headers=admin)


def test_zheng_sequential_loser_still_leaves_contention_trace(tmp_path):
    """竞争被调度成先后手时，后手败方仍须记争用痕，而不是无声的普通重复 400。

    种子实现里先成功者提交并释放锁，后手只吃到“已准排，勿重复”的 400，
    contention_log 为空——本用例据此见红。
    """
    c, _ = _clients(tmp_path)
    admin, writer = _tokens(c)
    sid = create_clean_batch(c, admin, "SM-ZHENG-SEQ")
    key = f"sample:{sid}"

    r1 = c.post(
        "/api/releases", headers=admin, json={"sample_id": sid, "officer": "admin"}
    )
    assert r1.status_code == 200, r1.text

    r2 = c.post(
        "/api/releases", headers=writer, json={"sample_id": sid, "officer": "writer"}
    )
    assert r2.status_code == 409, f"同批败方应按争用 409 处理，实际 {r2.status_code}: {r2.text}"

    assert formal_release_count(sid) == 1
    logs = contention_for(key)
    assert any(x.get("actor") == "writer" and x.get("action") == "争用失败" for x in logs), (
        f"败方只留争用痕，实际 contention={logs}"
    )


def test_zheng_concurrent_double_request_same_batch_single_winner(tmp_path):
    """真·并发双请求打同批准排：唯一胜者一行，败方 409 且有争用痕。"""
    c1, c2 = _clients(tmp_path)
    admin, writer = _tokens(c1)

    for trial in range(3):
        code = f"SM-ZHENG-C{trial}"
        sid = create_clean_batch(c1, admin, code)
        key = f"sample:{sid}"
        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, int, str]] = []

        def fire(client, headers, who):
            try:
                barrier.wait(timeout=10)
                r = client.post(
                    "/api/releases",
                    headers=headers,
                    json={"sample_id": sid, "officer": who},
                )
                outcomes.append((who, r.status_code, r.text[:200]))
            except Exception as exc:  # noqa: BLE001
                outcomes.append((who, -1, repr(exc)))

        t1 = threading.Thread(target=fire, args=(c1, admin, "admin"))
        t2 = threading.Thread(target=fire, args=(c2, writer, "writer"))
        t1.start()
        t2.start()
        t1.join(20)
        t2.join(20)

        statuses = sorted(s for _, s, _ in outcomes)
        wins = [o for o in outcomes if o[1] == 200]
        assert len(wins) == 1, f"第{trial}轮须恰有一个胜者：{outcomes}"
        assert all(s in (409,) for _, s, _ in outcomes if s != 200), outcomes
        assert formal_release_count(sid) == 1, outcomes

        logs = contention_for(key)
        assert any(x.get("action") == "争用失败" for x in logs), (
            f"第{trial}轮败方须留争用痕：{outcomes} logs={logs}"
        )


def test_zheng_ping_release_lock_script_exists_and_targets_batch():
    """并发脚本必须真实存在并暴露 ping_release_lock 打同批。"""
    assert SCRIPT.exists(), f"缺少并发脚本 {SCRIPT}"
    spec = importlib.util.spec_from_file_location("ping_release_lock", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert callable(getattr(mod, "ping_release_lock", None)), "脚本须定义 ping_release_lock"
