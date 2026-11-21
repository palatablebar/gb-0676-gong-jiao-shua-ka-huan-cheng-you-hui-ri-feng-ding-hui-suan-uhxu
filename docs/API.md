# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

# 采集水位账（离线分段补传）

裁决：续传水位只认连续到达，不越过缺口。每台车载机维护连续高水位 `contiguous_seq` 与待补段台账；越序段只暂存、不进换乘/封顶定稿，缺口区间在 `gap_ranges` 显式登记。

| 接口 | 方法 | 说明 |
|---|---|---|
| `/watermark` | GET | 水位账页面：连续区段、待补段、缺口、卡日应收与封存状态 |
| `/api/ingest/devices` | GET | 全部车载机水位台账 |
| `/api/ingest/devices/<id>` | GET | 单机台账（含分段、缺口、卡日应收、隔离事件） |
| `/api/ingest/devices/<id>/segments/<seq>` | POST | 补传一段：body 含 `payload`（`{"events":[...]}`）、可选 `checksum`、`expected_version` |
| `/api/ingest/devices/<id>/seal/<day>` | POST | 封存卡日；有待补段返回 409 `GAP_OPEN`，重复封存幂等 |
| `/api/ingest/devices/<id>/archive` | GET | 导出台账CSV（段状态、重试次数、校验和、缺口、卡日敞口） |
| `/api/ingest/demo/seed` `/fill-gap` `/reset` | POST | 演示数据：双机缺口场景、补达缺口段、重置 |

补传错误码：`CHECKSUM_MISMATCH`（校验和不符，422，不写入）、`SEGMENT_CONFLICT`（同序号不同内容，422，禁止覆盖）、`STALE_VERSION`（`expected_version` 与当前水位版本不符，409，整笔回滚、旧版保留）。同序号同校验和重试只累加 `duplicates`，不推进版本、不重复计入事件。
