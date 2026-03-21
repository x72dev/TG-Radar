from __future__ import annotations

from ..app.commands import CommandRegistry
from ..config import load_config, sync_snapshot_to_config, update_config_data
from ..services.formatters import render_help
from ..telegram_utils import bullet, escape, panel, section


def register_builtin_commands(registry: CommandRegistry) -> None:
    @registry.command('help', summary='查看命令帮助', usage='help')
    async def help_cmd(ctx):
        await ctx.app.reply_panel(ctx.event, render_help(ctx.app.config.cmd_prefix, registry.unique_specs()), auto_delete=0)

    @registry.command('ping', summary='检查前台命令延迟', usage='ping')
    async def ping_cmd(ctx):
        await ctx.app.reply_panel(ctx.event, ctx.app.render_ping_message(), auto_delete=0)

    @registry.command('status', summary='查看总体状态', usage='status')
    async def status_cmd(ctx):
        await ctx.app.reply_panel(ctx.event, ctx.app.render_status_message(), auto_delete=0)

    @registry.command('folders', summary='查看分组与缓存统计', usage='folders')
    async def folders_cmd(ctx):
        await ctx.app.reply_panel(ctx.event, ctx.app.render_folders_message(), auto_delete=0)

    @registry.command('rules', summary='查看某个分组的规则', usage='rules "分组名"')
    async def rules_cmd(ctx):
        folder = ctx.app.require_folder_arg(ctx)
        if folder is None:
            return
        await ctx.app.reply_panel(ctx.event, ctx.app.render_rules_message(folder), auto_delete=0)

    @registry.command('routes', summary='查看自动收纳规则', usage='routes')
    async def routes_cmd(ctx):
        await ctx.app.reply_panel(ctx.event, ctx.app.render_routes_message(), auto_delete=0)

    @registry.command('jobs', summary='查看后台任务', usage='jobs')
    async def jobs_cmd(ctx):
        await ctx.app.reply_panel(ctx.event, ctx.app.render_jobs_message(), auto_delete=0)

    @registry.command('log', summary='查看最近关键日志', usage='log [20]')
    async def log_cmd(ctx):
        limit = 20
        if ctx.tokens:
            try:
                limit = max(1, min(100, int(ctx.tokens[0])))
            except Exception:
                pass
        await ctx.app.reply_panel(ctx.event, ctx.app.render_log_message(limit), auto_delete=0)

    @registry.command('version', summary='查看版本信息', usage='version')
    async def version_cmd(ctx):
        await ctx.app.reply_panel(ctx.event, ctx.app.render_version_message(), auto_delete=0)

    @registry.command('enable', summary='启用一个分组的监控', usage='enable "分组名"', heavy=False)
    async def enable_cmd(ctx):
        folder = ctx.app.require_folder_arg(ctx)
        if folder is None:
            return
        ctx.app.db.set_folder_enabled(folder, True)
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('分组监控已开启', [section('当前动作', [bullet('分组', folder), bullet('状态', '开启', code=False)])]), auto_delete=0)

    @registry.command('disable', summary='停用一个分组的监控', usage='disable "分组名"', heavy=False)
    async def disable_cmd(ctx):
        folder = ctx.app.require_folder_arg(ctx)
        if folder is None:
            return
        ctx.app.db.set_folder_enabled(folder, False)
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('分组监控已关闭', [section('当前动作', [bullet('分组', folder), bullet('状态', '关闭', code=False)])]), auto_delete=0)

    @registry.command('addrule', summary='新增或追加规则', usage='addrule "分组名" "规则名" 关键词A 关键词B', heavy=False)
    async def addrule_cmd(ctx):
        if len(ctx.tokens) < 3:
            await ctx.app.reply_panel(ctx.event, panel('参数不足', [section('示例', [f'<code>{escape(ctx.app.config.cmd_prefix)}addrule "示例分组" "规则A" 关键词A 关键词B</code>'])]), auto_delete=0)
            return
        folder = ctx.app.resolve_folder(ctx.tokens[0]) or ctx.tokens[0]
        rule_name = ctx.tokens[1]
        pattern = ctx.app.pattern_from_input(' '.join(ctx.tokens[2:]))
        existing = ctx.app.db.get_rule(folder, rule_name)
        if existing:
            pattern = ctx.app.merge_rule_pattern(str(existing['pattern']), ' '.join(ctx.tokens[2:]))
        ctx.app.db.upsert_rule(folder, rule_name, pattern)
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('规则已保存', [section('规则详情', [bullet('分组', folder), bullet('规则', rule_name), bullet('表达式', pattern)])]), auto_delete=0)

    @registry.command('setrule', summary='整体覆盖规则', usage='setrule "分组名" "规则名" 新表达式', heavy=False)
    async def setrule_cmd(ctx):
        if len(ctx.tokens) < 3:
            await ctx.app.reply_panel(ctx.event, panel('参数不足', [section('示例', [f'<code>{escape(ctx.app.config.cmd_prefix)}setrule "示例分组" "规则A" 新表达式</code>'])]), auto_delete=0)
            return
        folder = ctx.app.resolve_folder(ctx.tokens[0]) or ctx.tokens[0]
        rule_name = ctx.tokens[1]
        pattern = ctx.app.pattern_from_input(' '.join(ctx.tokens[2:]))
        ctx.app.db.upsert_rule(folder, rule_name, pattern)
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('规则已更新', [section('规则详情', [bullet('分组', folder), bullet('规则', rule_name), bullet('表达式', pattern)])]), auto_delete=0)

    @registry.command('delrule', summary='删除规则或删除规则中的词', usage='delrule "分组名" "规则名" [词A 词B]', heavy=False)
    async def delrule_cmd(ctx):
        if len(ctx.tokens) < 2:
            await ctx.app.reply_panel(ctx.event, panel('参数不足', [section('示例', [f'<code>{escape(ctx.app.config.cmd_prefix)}delrule "示例分组" "规则A"</code>'])]), auto_delete=0)
            return
        folder = ctx.app.resolve_folder(ctx.tokens[0]) or ctx.tokens[0]
        rule_name = ctx.tokens[1]
        row = ctx.app.db.get_rule(folder, rule_name)
        if row is None:
            await ctx.app.reply_panel(ctx.event, panel('找不到规则', [section('定位信息', [bullet('分组', folder), bullet('规则', rule_name)])]), auto_delete=0)
            return
        if len(ctx.tokens) == 2:
            ctx.app.db.delete_rule(folder, rule_name)
            sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
            await ctx.app.reply_panel(ctx.event, panel('规则已删除', [section('删除结果', [bullet('分组', folder), bullet('规则', rule_name)])]), auto_delete=0)
            return
        pattern = ctx.app.remove_terms_from_pattern(str(row['pattern']), ' '.join(ctx.tokens[2:]))
        if not pattern:
            ctx.app.db.delete_rule(folder, rule_name)
            await ctx.app.reply_panel(ctx.event, panel('规则项已全部移除', [section('删除结果', [bullet('分组', folder), bullet('规则', rule_name)])]), auto_delete=0)
        else:
            ctx.app.db.upsert_rule(folder, rule_name, pattern)
            await ctx.app.reply_panel(ctx.event, panel('规则已裁剪', [section('新表达式', [bullet('分组', folder), bullet('规则', rule_name), bullet('表达式', pattern)])]), auto_delete=0)
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)

    @registry.command('addroute', summary='新增自动收纳规则', usage='addroute "分组名" 标题词A 标题词B', heavy=False)
    async def addroute_cmd(ctx):
        if len(ctx.tokens) < 2:
            await ctx.app.reply_panel(ctx.event, panel('参数不足', [section('示例', [f'<code>{escape(ctx.app.config.cmd_prefix)}addroute "示例分组" 标题词A 标题词B</code>'])]), auto_delete=0)
            return
        folder = ctx.app.resolve_folder(ctx.tokens[0]) or ctx.tokens[0]
        pattern = ctx.app.pattern_from_input(' '.join(ctx.tokens[1:]))
        if ctx.app.db.get_folder(folder) is None:
            ctx.app.db.upsert_folder(folder, folder_id=None, enabled=True)
        ctx.app.db.upsert_route(folder, pattern)
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('自动收纳规则已保存', [section('规则详情', [bullet('分组', folder), bullet('路由表达式', pattern)])]), auto_delete=0)

    @registry.command('delroute', summary='删除自动收纳规则', usage='delroute "分组名"', heavy=False)
    async def delroute_cmd(ctx):
        folder = ctx.app.require_folder_arg(ctx, allow_unknown=True)
        if folder is None:
            return
        if not ctx.app.db.delete_route(folder):
            await ctx.app.reply_panel(ctx.event, panel('没有找到该自动收纳规则', [section('定位信息', [bullet('分组', folder)])]), auto_delete=0)
            return
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('自动收纳规则已删除', [section('删除结果', [bullet('分组', folder)])]), auto_delete=0)

    @registry.command('setnotify', summary='设置分组通知目标', usage='setnotify "分组名" -1001234567890', heavy=False)
    async def setnotify_cmd(ctx):
        if len(ctx.tokens) < 2:
            await ctx.app.reply_panel(ctx.event, panel('参数不足', [section('示例', [f'<code>{escape(ctx.app.config.cmd_prefix)}setnotify "示例分组" -1001234567890</code>'])]), auto_delete=0)
            return
        folder = ctx.app.resolve_folder(ctx.tokens[0]) or ctx.tokens[0]
        ctx.app.db.set_folder_notify(folder, int(ctx.tokens[1]))
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('分组通知目标已更新', [section('结果', [bullet('分组', folder), bullet('notify_channel_id', ctx.tokens[1])])]), auto_delete=0)

    @registry.command('setalert', summary='设置分组告警目标', usage='setalert "分组名" -1001234567890', heavy=False)
    async def setalert_cmd(ctx):
        if len(ctx.tokens) < 2:
            await ctx.app.reply_panel(ctx.event, panel('参数不足', [section('示例', [f'<code>{escape(ctx.app.config.cmd_prefix)}setalert "示例分组" -1001234567890</code>'])]), auto_delete=0)
            return
        folder = ctx.app.resolve_folder(ctx.tokens[0]) or ctx.tokens[0]
        ctx.app.db.set_folder_alert(folder, int(ctx.tokens[1]))
        sync_snapshot_to_config(ctx.app.config.work_dir, ctx.app.db)
        await ctx.app.reply_panel(ctx.event, panel('分组告警目标已更新', [section('结果', [bullet('分组', folder), bullet('alert_channel_id', ctx.tokens[1])])]), auto_delete=0)

    @registry.command('setprefix', summary='修改 Telegram 命令前缀', usage='setprefix !', heavy=False)
    async def setprefix_cmd(ctx):
        if not ctx.tokens:
            await ctx.app.reply_panel(ctx.event, panel('参数不足', [section('示例', [f'<code>{escape(ctx.app.config.cmd_prefix)}setprefix !</code>'])]), auto_delete=0)
            return
        new_prefix = ctx.tokens[0]
        update_config_data(ctx.app.config.work_dir, lambda data: {**data, 'cmd_prefix': new_prefix})
        ctx.app.config = load_config(ctx.app.config.work_dir)
        await ctx.app.reply_panel(ctx.event, panel('命令前缀已更新', [section('结果', [bullet('新前缀', new_prefix, code=False)])], f'<i>下次使用请改为 <code>{escape(new_prefix)}help</code>。</i>'), auto_delete=0)

    @registry.command('sync', summary='执行一次分组同步', usage='sync', heavy=True)
    async def sync_cmd(ctx):
        await ctx.app.submit_heavy_job(ctx, 'sync', {'reply_to_event_id': ctx.event.id, 'trace': ctx.trace})

    @registry.command('routescan', summary='执行一次自动收纳扫描', usage='routescan', heavy=True)
    async def routescan_cmd(ctx):
        await ctx.app.submit_heavy_job(ctx, 'routescan', {'reply_to_event_id': ctx.event.id, 'trace': ctx.trace})

    @registry.command('update', summary='拉取仓库最新代码', usage='update', heavy=True)
    async def update_cmd(ctx):
        await ctx.app.submit_heavy_job(ctx, 'update_repo', {'reply_to_event_id': ctx.event.id, 'trace': ctx.trace})

    @registry.command('restart', summary='重启 admin/core 服务', usage='restart', heavy=True)
    async def restart_cmd(ctx):
        await ctx.app.submit_heavy_job(ctx, 'restart_services', {'reply_to_event_id': ctx.event.id, 'trace': ctx.trace})
