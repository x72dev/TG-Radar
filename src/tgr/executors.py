from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import sync_snapshot_to_config
from .sync_logic import scan_auto_routes, sync_dialog_folders


@dataclass(slots=True)
class JobResult:
    success: bool
    title: str
    details: list[str]
    footer: str = ''

    def summary(self) -> str:
        return f"{self.title}: {'ok' if self.success else 'failed'}"


async def execute_job(app, job) -> JobResult:
    payload: dict[str, Any] = job.payload
    jt = job.job_type
    if jt == 'sync':
        report = await sync_dialog_folders(app.worker_client, app.db, app.config)
        sync_snapshot_to_config(app.config.work_dir, app.db)
        app.reload_command_views()
        return JobResult(True, '分组同步完成', [report.summary()], '配置快照已回写。')
    if jt == 'routescan':
        report = await scan_auto_routes(app.worker_client, app.db, app.config)
        sync_snapshot_to_config(app.config.work_dir, app.db)
        app.reload_command_views()
        return JobResult(True, '自动收纳扫描完成', [report.summary()], '已尝试将命中的群组加入目标分组。')
    if jt == 'update_repo':
        cmd = ['git', '-C', str(app.config.work_dir), 'pull', '--ff-only']
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        text = (out or b'').decode('utf-8', errors='ignore').strip() or '无输出'
        return JobResult(proc.returncode == 0, '仓库更新完成' if proc.returncode == 0 else '仓库更新失败', [text[:1800]])
    if jt == 'restart_services':
        prefix = app.config.service_name_prefix
        cmd = ['systemctl', 'restart', f'{prefix}-admin.service', f'{prefix}-core.service']
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        text = (out or b'').decode('utf-8', errors='ignore').strip() or 'systemctl restart 已执行'
        return JobResult(proc.returncode == 0, '服务重启请求已执行' if proc.returncode == 0 else '服务重启失败', [text[:1800]])
    if jt == 'flush_snapshot':
        sync_snapshot_to_config(app.config.work_dir, app.db)
        return JobResult(True, '配置快照已刷新', ['folder_rules / auto_route_rules / _system_cache 已同步到 config.json'])
    return JobResult(False, '未知任务', [f'job_type={jt}'])
