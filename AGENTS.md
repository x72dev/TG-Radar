# AGENTS.md（TG-Radar 项目级执行规范）

## 1. 文档目的
本文件用于约束在 `TG-Radar` 仓库内工作的 AI/自动化代理行为，确保改动在以下维度保持一致：
- 架构一致性：单进程 + 单 `TelegramClient` + 事件驱动。
- 运行安全性：避免破坏 Telegram 会话、SQLite 数据和插件生态。
- 交付可验收：所有改动都具备最小验证步骤和回滚思路。

## 2. 项目定位与核心架构
- 项目类型：Telegram 风控/告警路由引擎。
- 核心运行形态：单进程异步应用（`src/tgr/app.py`），命令处理、消息监控、调度统一运行在同一事件循环。
- 关键依赖：
  - `Telethon`（Telegram 客户端）
  - `APScheduler`（后台任务调度）
  - `SQLite WAL`（状态与规则存储）

## 3. 目录职责约定
- `src/tgr/`：核心业务与框架代码（应用入口、调度、DB、插件系统）。
- `src/bootstrap_session.py`：Telegram 首次授权流程。
- `src/sync_once.py`：一次性同步流程。
- `src/radar.py`：主服务启动入口。
- `configs/`：插件配置文件（运行期可变）。
- `runtime/`：运行态数据（`radar.db`、日志、session、备份）。
- `plugins-external/`：外部插件仓库目录。

## 4. 标准运行命令
- 本地（项目根目录）：
  - `PYTHONPATH=src python3 src/bootstrap_session.py`：授权登录。
  - `PYTHONPATH=src python3 src/sync_once.py`：手动同步。
  - `PYTHONPATH=src python3 src/radar.py`：启动服务。
- Docker：
  - `docker compose run --rm tg-radar auth`
  - `docker compose run --rm tg-radar sync`
  - `docker compose up -d`
- 运维快捷命令（安装后）：
  - `TR status|start|stop|restart|sync|reauth|logs|doctor|update`

## 5. 开发总流程（必须遵守）
- 先定位：先用 `rg` 查调用链，再做改动。
- 小步改动：优先在单一责任文件落地，避免一次改动跨太多模块。
- 先编译后交付：至少执行 `python3 -m compileall src`。
- 变更可追溯：提交说明必须包含“改了什么/为什么改/如何验证”。

## 6. 代码规范
- Python 版本：3.10+。
- 异步规范：
  - I/O 逻辑使用 `async/await`，避免阻塞调用进入事件循环主路径。
  - 新增后台任务必须纳入现有调度/生命周期管理，不可游离。
- 数据层规范：
  - 所有写操作通过 `RadarDB` 事务接口，禁止裸连绕过封装。
  - 禁止改动导致 WAL 失效或并发写锁策略退化。
- 日志规范：
  - 关键路径错误必须落日志并给出可定位上下文。
  - 用户可见错误信息应短、可执行、避免泄露敏感字段。

## 7. Telethon 客户端硬性规则（高优先级）
- **禁止** 在业务代码中直接随意 `TelegramClient(...)` 创建连接。
- 必须统一使用：`tgr.telegram_client_factory.build_telegram_client(config)`。
- 统一要求显式携带以下参数：
  - `device_model`
  - `system_version`
  - `app_version`
- 可通过环境变量覆盖（用于会话稳定/风控调优）：
  - `TG_DEVICE_MODEL`
  - `TG_SYSTEM_VERSION`
  - `TG_APP_VERSION`

## 8. 插件开发与边界规范
- 插件必须通过 `PluginContext` 暴露的白名单能力访问系统：
  - DB：`ctx.db`
  - UI：`ctx.ui`
  - Bus：`ctx.bus`
- 禁止插件直接操作核心内部对象的私有实现。
- 新命令注册需声明：
  - `summary`
  - `usage`
  - `category`
  - 是否 `heavy`
- 插件异常必须可熔断，不得把核心进程拖垮。

## 9. 配置与敏感信息规范
- `config.json` 为真实运行配置，包含敏感凭据，**不得提交真实值**。
- 示例配置变更应同步维护：
  - `config.example.json`
  - `config.schema.json`
- 禁止在日志、面板、异常栈中输出完整 `api_hash`、session 文件内容或其他密钥。

## 10. 验收清单（最少）
- 语法检查：`python3 -m compileall src`。
- 行为检查：
  - 授权路径可启动（`bootstrap_session.py` 不报初始化参数错误）。
  - 主流程可启动（`src/radar.py` 启动至连接阶段无初始化异常）。
- 回归检查：
  - 插件加载无新增 ImportError。
  - `TR doctor` 关键项无新增失败项。

## 11. 常见故障处理准则
- 会话失效/撤销：
  - 先 `TR reauth`，再 `TR sync`，最后重启服务。
- 数据异常：
  - 优先检查 `runtime/radar.db` 与 `runtime/logs/`，不要直接删除运行目录。
- 插件异常：
  - 先执行插件级重载，必要时禁用问题插件，避免影响主循环。

## 12. 禁止事项
- 禁止无备份直接重置/清空 `runtime/`。
- 禁止在未经确认下修改生产 `config.json` 的凭据字段。
- 禁止绕过工厂函数创建 Telethon 客户端。
- 禁止把“修格式”与“改行为”混在一次变更里提交。

## 13. 变更提交模板（建议）
- 变更摘要：一句话说明目标。
- 影响范围：列出文件与模块。
- 风险评估：是否涉及会话、调度、DB、插件接口。
- 验证结果：执行的命令与结果摘要。
- 回滚方案：出现异常时如何快速恢复。

