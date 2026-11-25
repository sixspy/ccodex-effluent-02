# effluent-02 超标禁准裁决（开放超标 × 准排写口不得混用）

- 产品：污水排放批（effluent）
- 槽号：排污乙 · 第 2 槽
- 事故样本：**SM-B17**
- 裁决日期 / 封存日期：**2026-11-25（当日封存）**
- 状态：**已封取舍，禁止再走“第三写法”**

## 1. 事故口径

采样 SM-B17 的超标挂起册仍有 `status='开放'` 的未闭环行，准排写口却已硬落
一张 `discharge_releases.status='已准排'` 的正式行，样本批态被翻成“已准排”，
对账/证明据此把它读成“已准排”，超标页却未闭环。这是“开放超标”一侧与
“准排写口”一侧混用造成的假准排。

## 2. 已封取舍：甲路径为主，乙式影子意图不混表

经取舍，封 **甲路径** 为正式口径：

> 开放挂起仍在 → 准排写口正式行 **零增** 并 **拒写**；
> “超标禁准”裁决必须留审计痕（`audit_trail.action='超标禁准'`）。

同时保留 **乙路径兼容面**：准排意图可先登记为 `release_intents.status='影子草稿'`，
影子草稿 **不进** `discharge_releases`、对账永不计为已准排；只有原子升正
（同一事务内过同一道超标禁准闸、落唯一正式行、意图改“已升正”）才生效；
开放挂起仍在时升正失败，意图置“已作废”，正式册 **零改**。

**不采用“第三写法”**：禁止一边保留开放挂起、一边写正式准排行或把影子意图
直接统计成已准排。任何 `force/override/bypass_exceed` 等绕闸强准排企图，
一律写 `audit_trail.action='超标禁准偏航'` 并拒写。偏离已封取舍即记偏航。

## 3. 账实与争用

- **跨表同事务账实**：正式准排册、超标挂起册、对账齐套灯按同一口径读数；
  正式写口在同一事务内落正式行 + 翻批态 + 写审计。存储层加部分唯一索引
  `uq_release_formal_valid`，保证同一批至多一张 `status='已准排'` 正式行。
- **混用脏状态核销**：`POST /api/reconcile/rectify-open-hold-releases`
  在同一事务把“开放挂起 × 已准排”的硬行改挂
  `已作废（超标禁准）`、批态拉回 `旁路挂起`，并逐行记“超标禁准偏航”。
- **争用与取舍分写**：锁竞争只写 `contention_log.action='争用失败'`；
  裁决/偏航只写 `audit_trail`。两本账不混。
- **准排锁**：写口占 `sample:<id>` 锁；他会话持有即 409 且争用痕在抛错前
  提交（修复了原先争用痕随 `with connect()` 回滚丢失的缺陷）。同批并发
  双请求恰一胜、正式行恰一、败方 409 且只留争用痕。并发脚本
  `scripts/ping_release_lock.py` 的 `ping_release_lock` 打同批。

## 4. 五页同态口径

| 页面 | 同态保证 |
| --- | --- |
| 准排页 | 所选批有开放挂起时提交键 `disabled` 置灰并显示裁决文案；写口同步拒写 |
| 超标页 | 开放挂起列表非空时显示裁决横幅，逐行“⛔ 禁准排” |
| 采样页 | 每行给 `release_row_count/open_hold_count/ledger_aligned`，批态与准排册行数对齐 |
| 对账页 | 明细 `release_ready/released` 与聚合 `released_count/open_hold_count/complete_lamp` 只认账实有效准排，影子意图不计已准排 |
| 总览页 | `open_exceeds` 与 `GET /api/exceeds?status=开放` 同口径（认 `开放/开放挂起`） |

有效已准排的唯一真值：**正式准排册有“已准排”行 且 该批无开放超标挂起**。

## 5. 验收 / 复现

```bash
python -m pytest tests/test_effluent_02_jia.py \
                 tests/test_effluent_02_yi.py \
                 tests/test_effluent_02_zheng.py -v
python scripts/ping_release_lock.py            # 真起 uvicorn，并发双请求打同批
```

事故夹具 `SM-B17` 由三测文件共用的 `tests/effluent02_lib.py`
`seed_b17_accident()` 构造（开放挂起 + 硬准排正式行）。
