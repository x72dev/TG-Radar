from __future__ import annotations

import asyncio
import re
import shlex
import signal
import time
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient, events

from .app.commands import CommandContext, CommandRegistry
from .command_bus import CommandBus
from .compat import seed_db_from_legacy_config_if_needed
from .config import load_config, sync_snapshot_to_config
from .db import RadarDB
from .executors import JobResult
from .logger import setup_logger
from .plugins.builtin_commands import register_builtin_commands
from .scheduler import AdminScheduler
from .services.formatters import render_help, render_job_accept
from .services.message_io import MessageIO
from .sync_logic import RouteReport, SyncReport, scan_auto_routes, sync_dialog_folders
from .telegram_utils import (
    blockquote_preview,
    bullet,
    escape,
    format_duration,
    normalize_pattern_from_terms,
    panel,
    section,
    split_terms,
    try_remove_terms_from_pattern,
)
from .version import __version__


class AdminApp:
    def __init__(self, work_dir: Path) -> None:
        self.config = load_config(work_dir)
        self.logger = setup_logger('tg-radar-admin', self.config.logs_dir / 'admin.log')
        self.db = RadarDB(self.config.db_path)
        seed_db_from_legacy_config_if_needed(work_dir, self.db)
        self.started_at = time.monotonic()
        self.stop_event = asyncio.Event()
        self.command_client: TelegramClient | None = None
        self.worker_client: TelegramClient | None = None
        self.self_id: int | None = None
        self.command_bus = CommandBus(self.db, notifier=self._notify_scheduler)
        self.scheduler: AdminScheduler | None = None
        self.message_io: MessageIO | None = None
        self.registry = CommandRegistry()
        register_builtin_commands(self.registry)
        self._startup_sync_note = ''
        self.last_sync_result: tuple[SyncReport, RouteReport] | None = None

    def reload_command_views(self) -> None:
        self.config = load_config(self.config.work_dir)

    def _notify_scheduler(self) -> None:
        if self.scheduler is not None:
            self.scheduler.notify_new_job()

    def try_log_event(self, level: str, action: str, detail: str) -> None:
        try:
            self.db.try_log_event(level, action, detail)
        except Exception:
            pass

    def parse_tokens(self, args: str) -> list[str]:
        try:
            return shlex.split(args)
        except Exception:
            return [part for part in args.strip().split() if part]

    def pattern_from_input(self, raw: str) -> str:
        raw = raw.strip()
        if not raw:
            return ''
        terms = split_terms(raw)
        regexish = bool(re.search(r'[\.^$*+?{}\[\]|()]', raw))
        if len(terms) <= 1 and regexish:
            return raw
        return normalize_pattern_from_terms(terms)

    def merge_rule_pattern(self, existing: str, raw_terms: str) -> str:
        current_terms = split_terms(existing.replace('|', ' '))
        merged = []
        seen = set()
        for item in current_terms + split_terms(raw_terms):
            low = item.lower()
            if low in seen:
                continue
            seen.add(low)
            merged.append(item)
        return normalize_pattern_from_terms(merged)

    def remove_terms_from_pattern(self, existing: str, raw_terms: str) -> str:
        return try_remove_terms_from_pattern(existing, split_terms(raw_terms))

    def collect_folder_stats(self) -> dict[str, object]:
        rows = self.db.list_folders()
        enabled_rows = [row for row in rows if int(row['enabled']) == 1]
        total_cached = 0
        enabled_cached = 0
        total_rules = 0
        enabled_rules = 0
        folder_cards = []
        for row in rows:
            folder_name = str(row['folder_name'])
            cache_count = self.db.count_cache_for_folder(folder_name)
            rule_count = self.db.count_rules_for_folder(folder_name)
            total_cached += cache_count
            total_rules += rule_count
            if int(row['enabled']) == 1:
                enabled_cached += cache_count
                enabled_rules += rule_count
            folder_cards.append({
                'folder_name': folder_name,
                'enabled': int(row['enabled']) == 1,
                'folder_id': row['folder_id'],
                'cache_count': cache_count,
                'rule_count': rule_count,
                'notify_channel_id': row['notify_channel_id'],
                'alert_channel_id': row['alert_channel_id'],
            })
        return {
            'rows': rows,
            'folder_cards': folder_cards,
            'enabled_rows': enabled_rows,
            'total_folders': len(rows),
            'enabled_folders': len(enabled_rows),
            'total_cached': total_cached,
            'enabled_cached': enabled_cached,
            'total_rules': total_rules,
            'enabled_rules': enabled_rules,
            'route_count': len(self.db.list_routes()),
        }

    def render_help_message(self) -> str:
        return render_help(self.config.cmd_prefix, self.registry.unique_specs())

    def render_ping_message(self) -> str:
        uptime = format_duration(time.monotonic() - self.started_at)
        return panel('前台命令热路径状态', [section('运行状态', [bullet('版本', __version__, code=False), bullet('运行时长', uptime, code=False), bullet('命令前缀', self.config.cmd_prefix, code=False)])], '<i>轻命令直接回复，不再使用“编辑原消息”作为默认交互。</i>')

    def render_status_message(self) -> str:
        stats = self.collect_folder_stats()
        blocks = [
            section('总体统计', [
                bullet('全部分组', stats['total_folders']),
                bullet('已启用分组', stats['enabled_folders']),
                bullet('全部群组缓存', stats['total_cached']),
                bullet('已启用分组缓存', stats['enabled_cached']),
                bullet('规则总数', stats['total_rules']),
                bullet('已启用分组规则数', stats['enabled_rules']),
                bullet('自动收纳规则', stats['route_count']),
            ]),
            section('运行状态', [
                bullet('版本', __version__, code=False),
                bullet('operation_mode', self.config.operation_mode, code=False),
                bullet('last_sync', self.db.get_runtime_value('last_sync', '未执行'), code=False),
                bullet('last_route_scan', self.db.get_runtime_value('last_route_scan', '未执行'), code=False),
                bullet('last_core_reload', self.db.get_runtime_value('last_core_reload', '未记录'), code=False),
            ]),
        ]
        if self._startup_sync_note:
            blocks.append(section('启动前校准', [f'• {escape(self._startup_sync_note)}']))
        return panel('TG-Radar 状态总览', blocks)

    def render_folders_message(self) -> str:
        stats = self.collect_folder_stats()
        rows = []
        for item in stats['folder_cards']:
            label = '开启' if item['enabled'] else '关闭'
            rows.append(
                "\n".join([
                    f"<b>{escape(item['folder_name'])}</b>",
                    bullet('状态', label, code=False),
                    bullet('folder_id', item['folder_id'] if item['folder_id'] is not None else '未同步', code=False),
                    bullet('缓存群组', item['cache_count']),
                    bullet('规则数', item['rule_count']),
                    bullet('notify', item['notify_channel_id'] if item['notify_channel_id'] is not None else '默认', code=False),
                    bullet('alert', item['alert_channel_id'] if item['alert_channel_id'] is not None else '默认', code=False),
                ])
            )
        if not rows:
            rows = ['<i>当前没有分组记录。</i>']
        header = section('汇总', [
            bullet('全部分组', stats['total_folders']),
            bullet('全部群组缓存', stats['total_cached']),
            bullet('规则总数', stats['total_rules']),
        ])
        return panel('分组面板', [header, section('分组列表', rows)])

    def render_rules_message(self, folder: str) -> str:
        rules = self.db.list_rules(folder)
        if not rules:
            return panel(f'{folder} 的规则面板', [section('当前状态', ['<i>当前没有规则。</i>'])])
        rows = []
        for row in rules:
            rows.append("\n".join([
                f"<b>{escape(row['rule_name'])}</b>",
                bullet('表达式', row['pattern']),
                bullet('启用', '是' if int(row['enabled']) == 1 else '否', code=False),
            ]))
        return panel(f'{folder} 的规则面板', [section('已启用规则', rows)])

    def render_routes_message(self) -> str:
        routes = self.db.list_routes()
        if not routes:
            return panel('自动收纳规则面板', [section('当前状态', ['<i>当前没有自动收纳规则。</i>'])])
        rows = []
        for row in routes:
            rows.append("\n".join([f"<b>{escape(row['folder_name'])}</b>", bullet('pattern', row['pattern'])]))
        return panel('自动收纳规则面板', [section('规则列表', rows)])

    def render_jobs_message(self) -> str:
        jobs = self.db.list_jobs(20)
        if not jobs:
            return panel('后台任务队列', [section('当前状态', ['<i>当前没有任务。</i>'])])
        rows = []
        for job in jobs:
            rows.append("\n".join([
                f"<b>{escape(job.job_type)}</b>",
                bullet('任务 ID', job.id),
                bullet('状态', job.status, code=False),
                bullet('创建时间', job.created_at, code=False),
                bullet('摘要', job.result_summary or '—', code=False),
            ]))
        return panel('后台任务队列', [section('最近任务', rows)])

    def render_log_message(self, limit: int) -> str:
        rows = self.db.list_logs(limit)
        if not rows:
            return panel('关键日志', [section('当前状态', ['<i>暂无日志。</i>'])])
        blocks = []
        for row in rows:
            blocks.append(f"<b>{escape(row['created_at'])}</b> [{escape(row['level'])}] {escape(row['action'])}\n{blockquote_preview(str(row['detail']), 260)}")
        return panel('关键日志', [section(f'最近 {limit} 条', blocks)])

    def render_version_message(self) -> str:
        return panel('版本信息', [section('构建信息', [bullet('版本', __version__, code=False), bullet('模式', 'v2 refactor / modular admin', code=False)])])

    def resolve_folder(self, raw: str) -> str | None:
        raw = raw.strip()
        if not raw:
            return None
        rows = self.db.list_folders()
        for row in rows:
            if str(row['folder_name']) == raw:
                return raw
        matches = [str(row['folder_name']) for row in rows if str(row['folder_name']).lower().startswith(raw.lower())]
        if len(matches) == 1:
            return matches[0]
        return None

    async def reply_panel(self, event, text: str, *, auto_delete: int = 0) -> None:
        assert self.message_io is not None
        await self.message_io.reply(event, text, auto_delete=auto_delete)

    async def submit_heavy_job(self, ctx: CommandContext, job_type: str, payload: dict) -> None:
        payload = {**payload, 'source_command': ctx.command}
        job_id = self.command_bus.submit(job_type, payload)
        assert self.message_io is not None
        await self.message_io.reply(ctx.event, render_job_accept(ctx.command, ctx.trace, job_id), auto_delete=0)

    def require_folder_arg(self, ctx: CommandContext, *, allow_unknown: bool = False) -> str | None:
        if not ctx.tokens:
            asyncio.create_task(self.reply_panel(ctx.event, panel('参数不足', [section('提示', [f'• 先发送 <code>{escape(self.config.cmd_prefix)}folders</code> 查看分组。'])]), auto_delete=0))
            return None
        folder = self.resolve_folder(ctx.tokens[0]) or (ctx.tokens[0] if allow_unknown else None)
        if folder is None:
            asyncio.create_task(self.reply_panel(ctx.event, panel('找不到该分组', [section('提示', [f'• 先发送 <code>{escape(self.config.cmd_prefix)}folders</code> 查看列表。'])]), auto_delete=0))
        return folder

    def _make_trace_id(self) -> str:
        return datetime.now().strftime('cmd-%H%M%S-%f')

    def should_bootstrap_startup_snapshot(self) -> bool:
        stats = self.collect_folder_stats()
        if not stats['total_folders']:
            return True
        for item in stats['folder_cards']:
            if item['folder_id'] is None:
                return True
        return int(stats['total_cached']) == 0

    async def bootstrap_startup_snapshot_if_needed(self) -> None:
        if not self.should_bootstrap_startup_snapshot():
            self._startup_sync_note = '启动前校准未触发：本地已有有效快照。'
            return
        if self.worker_client is None:
            self._startup_sync_note = '启动前校准跳过：worker client 未就绪。'
            return
        try:
            sync_report = await sync_dialog_folders(self.worker_client, self.db, self.config)
            self.last_sync_result = (sync_report, RouteReport())
            sync_snapshot_to_config(self.config.work_dir, self.db)
            self._startup_sync_note = f"已完成启动前校准：识别 {len(sync_report.active)} 个分组，缓存 {sum(sync_report.active.values())} 个群 / 频道。"
        except Exception as exc:
            self._startup_sync_note = f'启动前校准失败：{exc}'
            self.logger.warning('startup bootstrap sync failed: %s', exc)

    async def send_startup_notification(self) -> None:
        assert self.message_io is not None
        stats = self.collect_folder_stats()
        rows = []
        for item in stats['folder_cards'][:10]:
            rows.append(f"• <b>{escape(item['folder_name'])}</b> · {escape('开启' if item['enabled'] else '关闭')} · 缓存 {item['cache_count']} · 规则 {item['rule_count']}")
        blocks = [
            section('总体统计', [
                bullet('全部分组', stats['total_folders']),
                bullet('已启用分组', stats['enabled_folders']),
                bullet('全部群组缓存', stats['total_cached']),
                bullet('已启用分组缓存', stats['enabled_cached']),
                bullet('规则总数', stats['total_rules']),
                bullet('自动收纳规则', stats['route_count']),
            ]),
            section('分组概览', rows or ['<i>暂无分组。</i>']),
        ]
        if self._startup_sync_note:
            blocks.append(section('启动前校准', [f'• {escape(self._startup_sync_note)}']))
        await self.message_io.notify(panel('TG-Radar admin 已启动', blocks), auto_delete=0)

    async def notify_job_result(self, job, result: JobResult) -> None:
        assert self.message_io is not None
        details = [f'• {escape(line)}' for line in result.details]
        footer = result.footer or '<i>该结果由后台任务独立回包，不会覆盖原命令消息。</i>'
        text = panel(result.title, [section('执行结果', details), section('任务信息', [bullet('任务 ID', job.id), bullet('任务类型', job.job_type, code=False)])], footer)
        await self.message_io.notify(text, auto_delete=0)

    async def notify_job_failure(self, job, exc: Exception) -> None:
        assert self.message_io is not None
        text = panel('后台任务执行失败', [section('错误信息', [blockquote_preview(str(exc), 500)]), section('任务信息', [bullet('任务 ID', job.id), bullet('任务类型', job.job_type, code=False)])], '<i>详细堆栈已写入 admin.log。</i>')
        await self.message_io.notify(text, auto_delete=0)

    async def is_saved_messages_command(self, event: events.NewMessage.Event) -> bool:
        if not getattr(event, 'out', False):
            return False
        if not getattr(event, 'is_private', False):
            return False
        try:
            chat_id = int(getattr(event, 'chat_id', 0) or 0)
            sender_id = int(getattr(event, 'sender_id', 0) or 0)
            return bool(self.self_id and chat_id == self.self_id and sender_id == self.self_id)
        except Exception:
            return False

    async def dispatch_command(self, event: events.NewMessage.Event, command: str, args: str, trace: str) -> None:
        spec = self.registry.get(command)
        if spec is None:
            await self.reply_panel(event, panel('未知命令', [section('提示', [f'• 发送 <code>{escape(self.config.cmd_prefix)}help</code> 查看帮助。'])]), auto_delete=0)
            return
        ctx = CommandContext(app=self, event=event, command=command, args=args, tokens=self.parse_tokens(args), trace=trace)
        await spec.handler(ctx)
        self.try_log_event('INFO', 'COMMAND', f'{trace} {command}')

    def register_handlers(self, client: TelegramClient) -> None:
        @client.on(events.NewMessage)
        async def control_panel(event: events.NewMessage.Event) -> None:
            text = (event.raw_text or '').strip()
            if not text or not text.startswith(self.config.cmd_prefix):
                return
            if not await self.is_saved_messages_command(event):
                return
            match = re.match(rf'^{re.escape(self.config.cmd_prefix)}(\w+)[ 	]*([\s\S]*)', text, re.IGNORECASE)
            if not match:
                return
            command = match.group(1).lower()
            args = (match.group(2) or '').strip()
            trace = self._make_trace_id()
            try:
                await self.dispatch_command(event, command, args, trace)
            except Exception as exc:
                self.logger.exception('command failed trace=%s: %s', trace, exc)
                await self.reply_panel(event, panel('TG-Radar 命令执行异常', [section('异常说明', [blockquote_preview(str(exc), 500)]), section('定位信息', [bullet('跟踪号', trace, code=False)])], '<i>详细堆栈已写入 admin.log，可在终端执行 <code>TR logs admin</code> 排查。</i>'), auto_delete=0)

    async def run(self) -> None:
        self.config.ensure_runtime_dirs()
        admin_session = self.config.admin_session.with_suffix('.session')
        worker_session = self.config.admin_worker_session.with_suffix('.session')
        if not admin_session.exists():
            raise FileNotFoundError('Missing runtime/sessions/tg_radar_admin.session. Run bootstrap_session.py first.')
        if not worker_session.exists():
            worker_session.write_bytes(admin_session.read_bytes())
        async with TelegramClient(str(self.config.admin_session), self.config.api_id, self.config.api_hash) as command_client, TelegramClient(str(self.config.admin_worker_session), self.config.api_id, self.config.api_hash) as worker_client:
            self.command_client = command_client
            self.worker_client = worker_client
            self.message_io = MessageIO(command_client, panel_auto_delete_seconds=self.config.panel_auto_delete_seconds)
            command_client.parse_mode = 'html'
            worker_client.parse_mode = 'html'
            me = await command_client.get_me()
            self.self_id = int(getattr(me, 'id', 0) or 0)
            self.register_handlers(command_client)
            await self.bootstrap_startup_snapshot_if_needed()
            self.db.log_event('INFO', 'ADMIN', f'TG-Radar admin started v{__version__}')
            await self.send_startup_notification()
            self.scheduler = AdminScheduler(self)
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, self.stop_event.set)
                except NotImplementedError:
                    pass
            tasks = [
                asyncio.create_task(self.scheduler.run()),
                asyncio.create_task(command_client.run_until_disconnected()),
                asyncio.create_task(self.stop_event.wait()),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            self.stop_event.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.db.log_event('INFO', 'ADMIN', 'admin service stopping')
            self.logger.info('TG-Radar admin service stopping')


async def run(work_dir: Path) -> None:
    app = AdminApp(work_dir)
    await app.run()
