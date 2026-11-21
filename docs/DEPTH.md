# 深度登记 公交刷卡换乘优惠与日封顶回算

规模：中型。使用者：公交清分中心复核员。核心规则：换乘按上次有效付费行程的时间窗计算，优惠与日封顶按确定顺序裁决并保留分币守恒。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 刷卡采信 | 查看并运行刷卡采信 | `/api/taps` | `app/modules/m1_taps/rules.py` | `tests/test_m1.py` |
| M2 线路换乘 | 查看并运行线路换乘 | `/api/routes` | `app/modules/m2_routes/rules.py` | `tests/test_m2.py` |
| M3 票价版本 | 查看并运行票价版本 | `/api/fares` | `app/modules/m3_fares/rules.py` | `tests/test_m3.py` |
| M4 行程配对 | 查看并运行行程配对 | `/api/journeys` | `app/modules/m4_journeys/rules.py` | `tests/test_m4.py` |
| M5 优惠回算 | 查看并运行优惠回算 | `/api/discounts` | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py` |
| M6 清分对账 | 查看并运行清分对账 | `/api/clearing` | `app/modules/m6_clearing/rules.py` | `tests/test_m6.py` |
| M7 日结归档 | 查看并运行日结归档 | `/api/settlement` | `app/modules/m7_settlement/rules.py` | `tests/test_m7.py` |
| M0 采集水位账 | 查看连续水位、待补段与卡日敞口，封存卡日 | `/watermark` | `app/ingest/service.py`、`app/ingest/receivables.py` | `tests/test_ingest.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 旧编码 | 默认解码失败 | 回退GB18030 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_legacy_encoding_real_sample` |
| TRAP-02 | BOM | 首列名多字符 | utf-8-sig | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bom_real_sample` |
| TRAP-03 | 带引号换行 | 拆成两条 | csv逻辑行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_quoted_newline_is_one_record` |
| TRAP-05 | 列数不匹配 | 静默错列 | 隔离原行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_bad_columns_real_sample` |
| TRAP-08 | 前导零ID | 编码变形 | 全程字符串 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_identifier_preserved_real_sample` |
| TRAP-14 | 重复键冲突 | 后写覆盖 | 保留首条并隔离冲突行 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_duplicate_conflict_real_sample` |
| TRAP-18 | 空态三态 | 一律成功 | 分别错误码 | `app/modules/m1_taps/rules.py` | `tests/test_m1.py::test_three_empty_states` |
| TRAP-26 | 旧版本提交 | 覆盖新结果 | 版本条件更新 | `app/store.py` | `tests/test_e2e.py::test_stale_review_409` |
| TRAP-06 | 全角数字 | 转换失败 | NFKC与Decimal | `app/values.py` | `tests/test_m3.py::test_thousands_and_fullwidth` |
| TRAP-07 | 金额哨兵 | 零参与扣费 | 空值隔离 | `app/values.py` | `tests/test_m3.py::test_real_sentinel_quarantined` |
| TRAP-11 | 混合偏移 | 落错版本 | UTC归整 | `app/values.py` | `tests/test_m3.py::test_timezone_equal` |
| TRAP-23 | 换乘截止点 | 重复优惠 | 截止点排除 | `app/modules/m5_discounts/rules.py` | `tests/test_m5.py::test_exact_window_excluded` |
| TRAP-25 | 票价重叠 | 随意取版 | 冲突隔离 | `app/modules/m3_fares/rules.py` | `tests/test_m3.py::test_overlap_rejected` |
| TRAP-30 | 离线补传越序 | 水位跟最新段走，缺口刷卡丢失 | 水位只认连续到达，越序段入待补台账 | `app/ingest/service.py` | `tests/test_ingest.py::test_watermark_only_advances_on_contiguous` |
| TRAP-31 | 同批反复上传 | 重复占额、换乘锚点错乱 | 同序号同校验和幂等，仅累加重试计数 | `app/ingest/service.py` | `tests/test_ingest.py::test_retry_same_segment_is_idempotent` |
| TRAP-32 | 同序号内容冲突 | 后写覆盖先到段 | 校验和比对，冲突422拒绝覆盖 | `app/ingest/service.py` | `tests/test_ingest.py::test_same_seq_different_checksum_conflicts` |
| TRAP-33 | 水位并发切换 | 旧版本请求覆盖新水位 | `expected_version` 乐观锁，409回滚保留旧版 | `app/ingest/service.py` | `tests/test_ingest.py::test_stale_expected_version_keeps_old_watermark` |
| TRAP-34 | 缺口期封存日结 | 未定稿数据被封存 | 待补段非空时封存409，补齐后按连续口径重算 | `app/ingest/service.py` | `tests/test_ingest.py::test_seal_blocked_while_gap_open_and_allowed_after_fill` |
| TRAP-35 | 待补段污染封顶 | 越序事件提前参与换乘/日封顶 | 待补段只计基础票价敞口，不进m5定稿 | `app/ingest/receivables.py` | `tests/test_ingest.py::test_pending_events_are_exposure_not_final_receivable` |

## 对账规则

输入原始逻辑行=最终采纳来源行+隔离来源行+重复行+空白行+分段表头。派生记录用_sources保存完整来源，因此分组不丢行。业务预期合计与上游申报合计之差等于逐行差异之和。结果按输入摘要、规则版本及参数产生唯一批次，事务写入；同源重跑只复用已有批次。

## 采集水位规则

每台车载机的续传水位 `contiguous_seq` 只在序号 1..N 全部到齐时推进到 N；越序到达的段写入待补段台账并在 `gap_ranges` 登记缺口，不进入 m2..m5 定稿，只按基础票价累计卡日待补敞口（`pending_exposure`）。同序号同 SHA-256 重试幂等（`duplicates` 累加、版本不变、事件不重复）；同序号异校验和拒绝覆盖；携带 `expected_version` 的写入在版本不符时整笔回滚。待补台账非空时任何卡日封存返回 409；缺口补齐后待补段自动转连续口径并重算换乘、封顶与卡日应收，封存快照带独立摘要且重复封存幂等。水位、分段、封存均在同一 SQLite 事务内更新。

## 已知不覆盖

4MB以上文件需预先按业务键分批；不模拟生产支付与外部回调。人工复核只变更批次状态，不改写原始记录。
