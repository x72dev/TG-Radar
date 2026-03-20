# 本次稳定性升级说明

- Admin 进程升级为命令层 / 调度层 / 执行层三层结构。
- 新增持久化后台任务表 `admin_jobs`，用于 sync / update / restart / snapshot flush 排队与去重。
- `-log` 继续保留关键事件视图。
- `config.json` 改为纯净结构，并新增 `config.schema.json`。
- 关键词告警保持长期保留，不自动回收。

## 本次主要文件

- `src/tgr/admin_service.py`
- `src/tgr/db.py`
- `src/tgr/config.py`
- `src/tgr/command_bus.py`
- `src/tgr/scheduler.py`
- `src/tgr/executors.py`
- `config.example.json`
- `config.schema.json`
