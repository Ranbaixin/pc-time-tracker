# PC Time Tracker

Windows 本地电脑使用时间追踪工具。追踪开机/关机时间和前台窗口活跃时间，生成统计报告，提供 REST API 和 HTML 仪表盘。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动追踪 + API 服务
python -m src.main run

# 打开仪表盘
# http://127.0.0.1:8080
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `python -m src.main run` | 启动追踪 + API（主模式） |
| `python -m src.main track` | 仅追踪（无 API） |
| `python -m src.main serve` | 仅 API（无追踪） |
| `python -m src.main stats` | 终端快速统计 |
| `python -m src.main config` | 查看当前配置 |
| `python -m src.main install` | 注册开机自启 |
| `python -m src.main uninstall` | 移除开机自启 |

## API 端点

Base URL: `http://127.0.0.1:8080/api/v1`

- `GET /sessions` — 会话列表
- `GET /sessions/current` — 当前会话
- `GET /activity` — 活动记录
- `GET /stats/summary` — 统计摘要
- `GET /stats/daily` — 每日统计
- `GET /stats/processes` — 进程排名
- `GET /stats/timeline` — 时间线
- `GET /agent/context` — Agent 综合上下文
- `GET /config` / `PUT /config` — 配置
- `GET /status` — 状态

完整 API 文档: `http://127.0.0.1:8080/docs`

## 配置

编辑 `data/config.json` 即可。首次运行自动从 `config/default.json` 复制。

## 数据存储

所有数据存储在本地 `data/tracker.db`（SQLite），不上传任何数据。
