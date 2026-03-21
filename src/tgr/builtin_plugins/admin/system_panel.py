PLUGIN_META = {
    "name": "system_panel",
    "version": "6.0.0",
    "description": "TR 管理器系统面板与插件管理命令",
    "author": "TG-Radar",
    "kind": "admin",
}

from tgr.telegram_utils import bullet, escape, panel, section


async def cmd_help(app, event, args):
    await app.safe_reply(event, app.render_help_message(), auto_delete=0)


async def cmd_plugins(app, event, args):
    await app.plugin_manager.run_healthchecks()
    await app.safe_reply(event, app.render_plugins_message(), auto_delete=0)


async def cmd_pluginreload(app, event, args):
    """Reload all admin plugins (and rediscover core plugins for stats)."""
    app.plugin_manager.load_admin_plugins()
    app.plugin_manager.load_core_plugins()
    await app.plugin_manager.run_healthchecks()
    await app.safe_reply(event, app.render_plugins_message(), auto_delete=0, prefer_edit=False)


async def cmd_reload(app, event, args):
    """Reload a single plugin by name."""
    name = args.strip()
    if not name:
        await app.safe_reply(event, panel("TR 管理器 · 参数不足", [section("示例", [
            f"<code>{app.config.cmd_prefix}reload keyword_monitor</code>",
            f"<code>{app.config.cmd_prefix}pluginreload</code> · 全量重载",
        ])]), prefer_edit=False)
        return
    ok, msg = app.plugin_manager.reload_plugin(name)
    title = "TR 管理器 · 插件重载成功" if ok else "TR 管理器 · 插件重载失败"
    await app.safe_reply(event, panel(title, [section("结果", [bullet("插件", name), bullet("状态", msg, code=False)])]), prefer_edit=False)
    if ok:
        app.db.log_event("INFO", "PLUGIN", f"reload: {name}")


async def cmd_pluginenable(app, event, args):
    """Enable a disabled plugin."""
    name = args.strip()
    if not name:
        await app.safe_reply(event, panel("TR 管理器 · 参数不足", [section("示例", [f"<code>{app.config.cmd_prefix}pluginenable 插件名</code>"])]), prefer_edit=False)
        return
    ok, msg = app.plugin_manager.enable_plugin(name)
    title = "TR 管理器 · 插件已启用" if ok else "TR 管理器 · 操作失败"
    await app.safe_reply(event, panel(title, [section("结果", [bullet("插件", name), bullet("状态", msg, code=False)])]), prefer_edit=False)
    if ok:
        app.db.log_event("INFO", "PLUGIN", f"enable: {name}")


async def cmd_plugindisable(app, event, args):
    """Disable a plugin (persists across restarts)."""
    name = args.strip()
    if not name:
        await app.safe_reply(event, panel("TR 管理器 · 参数不足", [section("示例", [f"<code>{app.config.cmd_prefix}plugindisable 插件名</code>"])]), prefer_edit=False)
        return
    ok, msg = app.plugin_manager.disable_plugin(name)
    title = "TR 管理器 · 插件已停用" if ok else "TR 管理器 · 操作失败"
    await app.safe_reply(event, panel(title, [section("结果", [bullet("插件", name), bullet("状态", msg, code=False)])]), prefer_edit=False)
    if ok:
        app.db.log_event("INFO", "PLUGIN", f"disable: {name}")


async def cmd_pluginconfig(app, event, args):
    """View or set plugin config: pluginconfig <name> [key] [value]"""
    parts = args.strip().split(None, 2)
    if not parts:
        await app.safe_reply(event, panel("TR 管理器 · 参数不足", [section("示例", [
            f"<code>{app.config.cmd_prefix}pluginconfig keyword_monitor</code> · 查看配置",
            f"<code>{app.config.cmd_prefix}pluginconfig keyword_monitor bot_filter false</code> · 修改",
        ])]), prefer_edit=False)
        return
    name = parts[0]
    record = app.plugin_manager.find_plugin(name)
    if record is None:
        await app.safe_reply(event, panel("TR 管理器 · 插件不存在", [section("提示", [f"· 发送 <code>{app.config.cmd_prefix}plugins</code> 查看列表。"])]), prefer_edit=False)
        return

    cfg = app.db.get_plugin_config(name)

    if len(parts) == 1:
        # Show config
        if not cfg and not record.config_schema:
            await app.safe_reply(event, panel(f"TR 管理器 · {escape(name)} 配置", [section("当前配置", ["· <i>该插件没有可配置项。</i>"])]), prefer_edit=False)
            return
        rows = []
        all_keys = set(cfg.keys()) | set(record.config_schema.keys())
        for key in sorted(all_keys):
            current = cfg.get(key)
            schema = record.config_schema.get(key, {})
            default = schema.get("default", "未设置")
            desc = schema.get("description", "")
            value_str = str(current) if current is not None else str(default)
            rows.append(f"· <b>{escape(key)}</b>：<code>{escape(value_str)}</code>" + (f" · {escape(desc)}" if desc else ""))
        await app.safe_reply(event, panel(f"TR 管理器 · {escape(name)} 配置", [section("配置项", rows)]), prefer_edit=False)
        return

    if len(parts) < 3:
        await app.safe_reply(event, panel("TR 管理器 · 参数不足", [section("示例", [f"<code>{app.config.cmd_prefix}pluginconfig {name} key value</code>"])]), prefer_edit=False)
        return

    key, raw_value = parts[1], parts[2]
    # Auto type conversion
    if raw_value.lower() in {"true", "yes", "on", "1"}:
        value = True
    elif raw_value.lower() in {"false", "no", "off", "0"}:
        value = False
    else:
        try:
            value = int(raw_value)
        except ValueError:
            try:
                value = float(raw_value)
            except ValueError:
                value = raw_value

    cfg[key] = value
    app.db.set_plugin_config(name, cfg)
    await app.safe_reply(event, panel("TR 管理器 · 配置已更新", [section("修改详情", [bullet("插件", name), bullet("配置项", key), bullet("新值", str(value))])]), prefer_edit=False)
    app.db.log_event("INFO", "PLUGIN", f"config: {name}.{key}={value}")


def setup(ctx):
    ctx.register_command("help", cmd_help, summary="查看已注册命令总表", usage="help", category="系统面板")
    ctx.register_command("plugins", cmd_plugins, summary="查看全部插件运行状态", usage="plugins", category="系统面板")
    ctx.register_command("pluginreload", cmd_pluginreload, summary="全量重新加载所有插件", usage="pluginreload", category="插件管理")
    ctx.register_command("reload", cmd_reload, summary="重载单个插件", usage="reload 插件名", category="插件管理")
    ctx.register_command("pluginenable", cmd_pluginenable, summary="启用已停用的插件", usage="pluginenable 插件名", category="插件管理")
    ctx.register_command("plugindisable", cmd_plugindisable, summary="停用插件（重启保持停用）", usage="plugindisable 插件名", category="插件管理")
    ctx.register_command("pluginconfig", cmd_pluginconfig, summary="查看或修改插件配置", usage="pluginconfig 插件名 [键] [值]", category="插件管理")
