# 污水排放批（effluent）

数据工程母仓：处理厂采样入库 → 超标旁路挂起 → 准排放裁定 → 封批升代 → 排放许可对账。

## 骨架一句话

采样批次入库后裁定超标才准排放封批，驳回进旁路挂起，指标血缘与排放许可对账。

## 一键起环境

```bash
docker compose up -d --build
```

浏览器访问 http://localhost:11600

- 管理账号：`admin` / `admin123`
- 读写账号：`writer` / `writer123`
- 只读账号：`viewer` / `viewer123`

## 本地开发

```powershell
cd projects/14-effluent
$env:PYTHONPATH="backend"
python -m uvicorn app:app --app-dir backend --reload --port 8000
```

## 自测

```powershell
$env:PYTHONPATH="backend"
python -m pytest
python scripts/count_loc.py
```

## effluent-02 超标禁准裁决（SM-B17）

开放超标未闭环时，准排写口正式准排册**零增并拒写**（甲路径，已封存取舍见
[`docs/effluent-02-超标禁准裁决.md`](docs/effluent-02-超标禁准裁决.md)，
当日封存印见 `docs/seals/`）。有效已准排口径：正式准排册有“已准排”行
**且**该批无开放超标挂起；影子准排意图未原子升正不计已准排。强准排/绕闸记
`超标禁准偏航`，锁竞争记 `contention_log`（争用与取舍分写）。

```bash
python -m pytest tests/test_effluent_02_jia.py \
                 tests/test_effluent_02_yi.py \
                 tests/test_effluent_02_zheng.py -v
python scripts/ping_release_lock.py     # 两路会话并发打同批准排锁
```

混用脏状态（开放挂起 × 硬准排）可用
`POST /api/reconcile/rectify-open-hold-releases` 同事务核销。
