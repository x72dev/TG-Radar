# TG-Radar v6.0.0

Telegram 关键词监控系统，采用 PagerMaid-Pyro 风格的全解耦插件化架构。

## 架构概览

```
┌─────────────────────────────────────────────┐
│               Admin 进程                      │
│  Telegram 命令 → PluginManager → 调度器       │
│                     ↓                         │
│              CommandBus → Executor             │
└─────────────────────────────────────────────┘
        ↕ SQLite (WAL)   ↕ SIGUSR1 信号
┌─────────────────────────────────────────────┐
│                Core 进程                      │
│  全量消息监听 → PluginManager → 告警发送      │
└─────────────────────────────────────────────┘
```

## 核心特性

- **双进程架构**：Admin（命令 + 调度）与 Core（消息监听）完全独立
- **全解耦插件系统**：命令、钩子、健康检查均通过插件注册
- **完整插件生命周期**：发现 → 加载 → 运行 → 熔断 → 停用 → 重载
- **单插件热重载**：`-reload 插件名` 只影响一个插件
- **错误熔断**：插件连续失败 N 次后自动停用，修复后 `-reload` 恢复
- **持久化插件状态**：启用/停用状态重启不丢失
- **插件级配置**：每个插件可以有独立的配置项
- **轻/重命令分离**：轻命令直接回复，重命令进入后台调度队列

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/chenmo8848/TG-Radar.git
cd TG-Radar

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置
cp config.example.json config.json
# 编辑 config.json，填入 api_id 和 api_hash

# 4. 首次授权
python src/bootstrap_session.py

# 5. 启动
python src/radar_admin.py   # Admin 进程
python src/radar_core.py    # Core 进程（另一个终端）
```

## 插件管理命令

| 命令 | 说明 |
|------|------|
| `-plugins` | 查看所有插件运行状态 |
| `-reload <名称>` | 热重载单个插件 |
| `-pluginreload` | 全量重载所有插件 |
| `-pluginenable <名称>` | 启用已停用的插件 |
| `-plugindisable <名称>` | 停用插件（持久化） |
| `-pluginconfig <名称> [键] [值]` | 查看/修改插件配置 |

## v6.0.0 更新日志

### 架构重建
- 插件系统完全重建，支持 PagerMaid-Pyro 风格的全生命周期管理
- 新增 `plugin_state` 数据库表，插件启停状态持久化
- 新增错误熔断机制（连续失败自动停用）
- 新增 `teardown()` 和 `register_cleanup()` 卸载钩子
- 新增单插件热重载（`-reload 插件名`）
- 新增插件级配置系统（`-pluginconfig`）
- 新增 PLUGIN_META 标准（depends / conflicts / config_schema / min_core_version）

### Bug 修复
- **BUG-01**: 修复 SQLite 连接泄漏（所有只读方法现在正确关闭连接）
- **BUG-02**: 修复 `upsert_folder` 对已存在行 enabled/alert 不更新的问题
- **BUG-04**: 修复 `shlex.split` 未捕获引号异常导致的错误面板
- **BUG-05**: 修复 `revision_poll_seconds=0` 时轮询无法关闭（改用 -1 禁用）
- **BUG-06**: 修复 `apply_route_task` 创建分组后 DB 记录的 folder_id 不一致
- **BUG-07**: 修复 config reload 后 executor/scheduler 持有过期引用

### 安全改进
- **SEC-01**: `service_name_prefix` 增加正则校验，防止 shell 注入
- **SEC-02**: `setprefix` 增加 HTML 特殊字符过滤

### 性能优化
- **PERF-01/02**: 启动通知和状态面板使用批量 COUNT 查询替代 N+1

### 健壮性
- Core 进程启动时写入 PID 文件，reload 信号可精确定位
- `apply_route_task` 增加操作间隔，减少 FloodWait 风险
- 告警渲染函数从 `core_service.py` 移到 `telegram_utils.py`，插件不再依赖服务入口

## 目录结构

```
TG-Radar/
├── config.example.json
├── requirements.txt
├── src/
│   ├── radar_admin.py          # Admin 入口
│   ├── radar_core.py           # Core 入口
│   ├── bootstrap_session.py    # 授权向导
│   ├── sync_once.py            # 一次性同步
│   └── tgr/
│       ├── admin_service.py    # Admin 服务编排
│       ├── core_service.py     # Core 服务编排
│       ├── command_bus.py      # 命令总线
│       ├── config.py           # 配置系统
│       ├── compat.py           # 迁移兼容
│       ├── db.py               # 数据库层
│       ├── executors.py        # 任务执行器
│       ├── logger.py           # 日志
│       ├── scheduler.py        # 调度器
│       ├── sync_logic.py       # 同步引擎
│       ├── telegram_utils.py   # 工具函数
│       ├── version.py          # 版本号
│       ├── core/
│       │   └── plugin_system.py  # 插件系统（核心）
│       └── builtin_plugins/
│           └── admin/
│               └── system_panel.py  # 内置插件管理命令
└── plugins-external/
    └── TG-Radar-Plugins/       # 外部插件仓库
        └── plugins/
            ├── admin/           # Admin 插件
            └── core/            # Core 插件
```
