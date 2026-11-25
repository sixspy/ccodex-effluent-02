#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""effluent-02 争用脚本：两路会话并发打同一批准排（ping_release_lock）。

用法：
  # 自带临时库 + 起本机 uvicorn，造一个干净可准排批后并发双请求：
  python scripts/ping_release_lock.py

  # 打已在跑的服务、指定批次：
  python scripts/ping_release_lock.py --base-url http://127.0.0.1:8123 --sample-id 4

判据（effluent-02 争用闸）：
  恰一个会话 200；正式准排册该批恰一行；败方非 200 且 contention_log
  留有 action='争用失败'、lock_key='sample:<id>' 的争用痕。满足退出 0，否则 1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]


def http(method: str, base: str, path: str, token: str | None = None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return resp.status, json.loads(raw)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8") or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def login(base: str, username: str, password: str) -> str:
    status, data = http(
        "POST", base, "/api/login", body={"username": username, "password": password}
    )
    if status != 200:
        raise RuntimeError(f"login {username} failed: {status} {data}")
    return data["token"]


def ping_release_lock(
    base: str,
    sample_id: int,
    token: str | None = None,
    officer: str | None = None,
):
    """对同一批准排写口发一次准排（探/抢 sample:<id> 准排锁）。

    token 可省略：省略时用 admin/admin123 登录（便于最简调用）。
    """
    if token is None:
        token = login(base, "admin", "admin123")
    return http(
        "POST",
        base,
        "/api/releases",
        token=token,
        body={"sample_id": sample_id, "officer": officer or "ping_release_lock"},
    )


def evaluate(base: str, sample_id: int, admin_token: str) -> dict:
    _, locks_payload = http("GET", base, "/api/locks", token=admin_token)
    key = f"sample:{sample_id}"
    traces = [
        x
        for x in locks_payload.get("contention", [])
        if x.get("lock_key") == key and x.get("action") == "争用失败"
    ]
    _, releases_payload = http("GET", base, "/api/releases", token=admin_token)
    formal = [
        x
        for x in releases_payload.get("items", [])
        if x.get("sample_id") == sample_id and x.get("status") == "已准排"
    ]
    return {
        "lock_key": key,
        "formal_rows": len(formal),
        "contention_traces": traces,
        "loser_traced": len(traces) >= 1,
    }


def run_pair(
    base: str,
    sample_id: int,
    user_a=("admin", "admin123"),
    user_b=("writer", "writer123"),
) -> dict:
    tok_a = login(base, *user_a)
    tok_b = login(base, *user_b)
    barrier = threading.Barrier(2)
    outcomes: list[dict] = []

    def worker(who: str, token: str):
        barrier.wait(timeout=10)
        status, payload = ping_release_lock(base, sample_id, token, officer=who)
        outcomes.append({"who": who, "status": status, "detail": payload.get("detail")})

    ta = threading.Thread(target=worker, args=(user_a[0], tok_a))
    tb = threading.Thread(target=worker, args=(user_b[0], tok_b))
    ta.start()
    tb.start()
    ta.join(30)
    tb.join(30)

    verdict = evaluate(base, sample_id, tok_a)
    wins = [o for o in outcomes if o["status"] == 200]
    verdict.update(
        {
            "sample_id": sample_id,
            "outcomes": sorted(outcomes, key=lambda x: x["who"]),
            "winner_count": len(wins),
            "single_winner": len(wins) == 1,
        }
    )
    verdict["ok"] = (
        verdict["single_winner"]
        and verdict["formal_rows"] == 1
        and verdict["loser_traced"]
    )
    return verdict


def _self_host_and_run(port: int) -> dict:
    tmp_db = Path(tempfile.mkdtemp(prefix="ping_lock_")) / "ping.db"
    os.environ["EFFLUENT_DB"] = str(tmp_db)
    sys.path.insert(0, str(ROOT / "backend"))
    import uvicorn  # noqa: E402

    from app import app  # noqa: E402
    from db import init_db  # noqa: E402

    init_db()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            status, _ = http("GET", base, "/api/health")
            if status == 200:
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("self-hosted server did not become healthy")

    admin = login(base, "admin", "admin123")
    _, outfalls = http("GET", base, "/api/outfalls", token=admin)
    of = next(x for x in outfalls["items"] if x["outfall_code"] == "OF-E1-A")
    _, created = http(
        "POST",
        base,
        "/api/samples",
        token=admin,
        body={
            "outfall_id": of["id"],
            "permit_ref": "DP-2026-E01",
            "sample_code": "SM-PING-LOCK",
            "flow_m3h": 120,
            "metrics": {"cod": 20.0, "nh3n": 1.2, "tp": 0.1},
        },
    )
    sid = created["id"]
    try:
        return run_pair(base, sid)
    finally:
        server.should_exit = True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="effluent-02 ping_release_lock 争用脚本")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--sample-id", type=int, default=None)
    parser.add_argument("--port", type=int, default=8137)
    args = parser.parse_args(argv)

    if args.base_url:
        if args.sample_id is None:
            parser.error("--sample-id is required with --base-url")
        report = run_pair(args.base_url, args.sample_id)
    else:
        report = _self_host_and_run(args.port)

    print("PING_RELEASE_LOCK_REPORT " + json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
