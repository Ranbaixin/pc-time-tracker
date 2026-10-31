# Agent context 数据新鲜度与采集覆盖

基线：`master` / `004e24b808680cb6b72512d09b6850864ef197b6`。

## API 契约

`GET /api/v1/agent/context` 保留原有字段及统计口径，新增：

- `data._metadata.collection_quality`：持久化的桌面采集证据。
- `data._metadata.current_session_is_live`：当前数据库会话是否匹配最近且未过期的成功采样。

`generated_at` 仍仅代表本次响应生成时间。最近采样时间是
`collection_quality.last_successful_collection_at`，没有证据时为 `null`。
API 读取、浏览器上传、活动关闭时间、会话开始时间、推断的睡眠记录均不刷新它。
服务以 `track` 独立运行、API 以 `serve` 读取同一个数据库时也可获得相同证据。

| collection_status | 含义 |
| --- | --- |
| `collecting` | 最近一次成功轮询记录了前台活动，且仍在新鲜度阈值内 |
| `idle` | 最近一次成功轮询检测到空闲；只有成功采样才能得出此状态 |
| `no_activity` | 成功轮询但空标题被配置过滤；不代表用户正在休息 |
| `interrupted` | 已知轮询异常/正常停止，或成功采样超过新鲜度阈值 |
| `historical_stale` | 最近成功采样距今超过 24 小时 |
| `coverage_unknown` | 没有采样证据（包括旧版本历史数据），或最近采样时间在未来 |

`freshness` 分别为 `fresh` / `expired` / `stale` / `unknown`。
`age_seconds` 为响应时刻减最近成功采样时间，时钟回拨时可以为负。
新鲜度阈值由成功采样当时的 `poll_interval_seconds * sleep_gap_threshold_multiplier`
确定，并在响应中明示；24 小时为本版历史过期策略，不表示历史记录被删除。
阈值内属于有界的新鲜度判断，无法即时发现上一次采样之后的突然崩溃。

`coverage.today` 覆盖本机今日零点至响应时刻，`coverage.last_7_days` 覆盖六天前
零点至响应时刻。每个窗口提供 `status`（`complete` / `partial` / `unknown`）、
`covered_seconds`、`observed_intervals` 和 `unknown_intervals`。
覆盖仅累计同一采集器、同一会话、同一阈值下连续成功轮询之间的区间。
间距必须同时满足墙上时钟与单调时钟的阈值检查。

首次样本只证明一个时间点，覆盖时长为零。最新样本至响应时刻仍是未知，
即使采样仍新鲜，覆盖也可能是 `partial`。区间按查询窗口裁切后合并，
重启、异常、超时、会话切换或配置阈值变化不会被跨接；覆盖不反推到开机时间。
未知间隔表示缺少成功采样证据，不区分用户离开、停机、锁屏或真实睡眠。
`activity_record_bounds` 只描述桌面/system 原始记录的时间边界，绝不作为采集证据。

所有时间沿用现有数据库的**本机本地无时区时间**，响应提供
`time_basis=machine_local_naive`。本版不迁移历史时间戳或解决跨时区/DST 同步。

失去新鲜证据或会话不匹配时，context 的 `current_session.active_window` 返回
`null`；会话对象其他字段保持原有数据库含义，不能单凭 `is_active` 推断采集器存活。
`text_summary` 在覆盖不足时附加可信度提示。下游应先检查质量信息，再做行为推断。
本次不改变 stats、sessions 等其他端点的既有语义。

## 存储与升级

Schema v7 新增独立 `collection_periods` 表；正常连续轮询更新同一行，
中断后追加新行。正常停止与捕获的异常标记旧区间为中断，不伪造成功时间。
该表独立于活动表，不存窗口标题；没有级联删除历史证据的外键。
异常写入不推进内存中的成功时间；中断标记写入失败也会在内存中断开区间，
持久化旧成功时间按阈值过期，恢复后重新开段。

已有数据库升级前创建 `tracker-pre-migration-v7-*.db` 一致性备份。
迁移可重复执行，不回填猜测的成功采样，不修改旧活动或会话。
桌面仍是总时长的权威来源，浏览器仅补充内容维度；本次不实现事件幂等同步。
历史开放活动仍可能按原统计逻辑延长时长，API 的质量提示明确说明此限制。

## 实际验证（2026-10-31）

环境：Linux / Python 3.12.14，FastAPI 0.162.0，Starlette 1.10.0，pytest 8.4.2。
依赖使用仓库允许的版本范围安装。Linux 上仅为导入设置了 `ctypes.windll`
占位对象，未模拟真实 Win32 行为；引擎测试使用仓库的 FakeWindows/FakeClock。

- Ruff：`python -m ruff check src mcp_server tests` 通过。
- 针对性回归：38 passed；`src.collection` 语句覆盖率 100%。
- 全量：61 passed / 2 failed；原始基线同环境：47 passed / 2 failed。
- 两个共同失败分别是 `test_create_app_respects_dashboard_flag_and_registers_api`
  （新 FastAPI 的 `_IncludedRouter` 没有 `.path`），以及
  `test_enable_generates_portable_quoted_launcher`（Linux 路径分隔符不满足 Windows 断言）。
  本次未修改这两项无关行为。全量测试在目录名为 `pc-time-tracker` 的副本中运行，
  因现有另一个测试断言了仓库目录名。

针对性覆盖正常采集、窗口不变、空闲、过滤窗口、超时、恢复、短间隔重启、
异常处理、磁盘失败、空库、旧开放记录、过期、时钟回拨、跨零点裁切、区间合并、
API 新鲜度与生成时间隔离、迁移保真和桌面/浏览器统计回归。

Linux 测试命令（占位对象仅用于模块导入）：

```bash
python -c 'import ctypes, types; ctypes.windll = types.SimpleNamespace(user32=types.SimpleNamespace(), kernel32=types.SimpleNamespace()); import pytest; raise SystemExit(pytest.main(["-q", "tests/test_collection_quality.py", "tests/test_tracker.py", "tests/test_database.py", "tests/test_api_contract.py", "tests/test_stats.py", "tests/test_browser_activity.py", "--cov=src.collection", "--cov-report=term-missing"]))'
```

## 真实 Windows 验收状态

以下均为**未执行 / 待人工验收**，不以模拟测试代替：

- 锁屏期间 Win32 空闲/前台返回值是否符合预期，不能将被过滤窗口解释为休息。
- 休眠/唤醒后最近采样刷新，休眠间隔保留未知覆盖，而非认证为完整采集。
- 杀进程、同次开机重启、自启动重启系统：过期后隐藏当前窗口，恢复后新开覆盖区间。
- `track` 与 `serve` 分进程共享数据库，以及连续运行 2～3 天的写入/备份开销。

Win32 适配层原有的部分失败回退（例如无法读取前台标题）保持不变。
“成功”指现有采集处理与证据写入均完成，不认证每个底层 Win32 返回值的真实性。
