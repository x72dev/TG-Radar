PLUGIN_META = {"name": "system_panel", "version": "6.0.0", "description": "系统面板与插件管理", "kind": "admin"}

def setup(ctx):
    ui, log = ctx.ui, ctx.log

    @ctx.command("help", summary="查看命令列表", usage="help", category="系统")
    async def _(app, event, args):
        await ctx.reply(event, app.render_help_message(), auto_delete=0)

    @ctx.command("plugins", summary="查看插件状态", usage="plugins", category="系统")
    async def _(app, event, args):
        await app.plugin_manager.run_healthchecks()
        await ctx.reply(event, app.render_plugins_message(), auto_delete=0)

    @ctx.command("pluginreload", summary="全量重载插件", usage="pluginreload", category="插件管理")
    async def _(app, event, args):
        app.plugin_manager.load_admin_plugins()
        app.plugin_manager.load_core_plugins()
        await app.plugin_manager.run_healthchecks()
        log.info("全量重载完成")
        await ctx.reply(event, app.render_plugins_message(), auto_delete=0, prefer_edit=False)

    @ctx.command("reload", summary="重载单个插件", usage="reload 插件名", category="插件管理")
    async def _(app, event, args):
        name = args.strip()
        if not name:
            return await ctx.reply(event, ui.panel("TG-Radar · 参数不足", [ui.section("用法", [f"<code>{app.config.cmd_prefix}reload 插件名</code>"])]), prefer_edit=False)
        ok, msg = app.plugin_manager.reload_plugin(name)
        await ctx.reply(event, ui.panel("TG-Radar · 重载" + ("成功" if ok else "失败"), [ui.section("结果", [ui.bullet("插件", name), ui.bullet("状态", msg, code=False)])]), prefer_edit=False)
        if ok: ctx.db.log_event("INFO", "PLUGIN", f"reload: {name}")

    @ctx.command("pluginenable", summary="启用插件", usage="pluginenable 插件名", category="插件管理")
    async def _(app, event, args):
        name = args.strip()
        if not name:
            return await ctx.reply(event, ui.panel("TG-Radar · 参数不足", []), prefer_edit=False)
        ok, msg = app.plugin_manager.enable_plugin(name)
        await ctx.reply(event, ui.panel("TG-Radar · " + ("已启用" if ok else "失败"), [ui.section("结果", [ui.bullet("插件", name), ui.bullet("状态", msg, code=False)])]), prefer_edit=False)

    @ctx.command("plugindisable", summary="停用插件", usage="plugindisable 插件名", category="插件管理")
    async def _(app, event, args):
        name = args.strip()
        if not name:
            return await ctx.reply(event, ui.panel("TG-Radar · 参数不足", []), prefer_edit=False)
        ok, msg = app.plugin_manager.disable_plugin(name)
        await ctx.reply(event, ui.panel("TG-Radar · " + ("已停用" if ok else "失败"), [ui.section("结果", [ui.bullet("插件", name), ui.bullet("状态", msg, code=False)])]), prefer_edit=False)

    @ctx.command("pluginconfig", summary="查看/修改插件配置", usage="pluginconfig 插件 [键] [值]", category="插件管理")
    async def _(app, event, args):
        parts = args.strip().split(None, 2)
        if not parts:
            return await ctx.reply(event, ui.panel("TG-Radar · 用法", [ui.section("示例", [f"<code>{app.config.cmd_prefix}pluginconfig 插件名</code> 查看", f"<code>{app.config.cmd_prefix}pluginconfig 插件名 键 值</code> 修改"])]), prefer_edit=False)
        name = parts[0]
        rec = app.plugin_manager.find_plugin(name)
        if not rec:
            return await ctx.reply(event, ui.panel("TG-Radar · 找不到插件", []), prefer_edit=False)
        pcfg = app.plugin_manager.get_plugin_config_file(name, rec.config_schema)
        if len(parts) == 1:
            data, schema = pcfg.all(), pcfg.schema()
            keys = sorted(set(data) | set(schema))
            if not keys:
                return await ctx.reply(event, ui.panel(f"TG-Radar · {ui.escape(name)}", [ui.section("配置", ["<i>无可配置项</i>"])]), prefer_edit=False)
            rows = []
            for k in keys:
                v = data.get(k)
                s = schema.get(k, {})
                rows.append(f"· <b>{ui.escape(k)}</b>  <code>{ui.escape(v if v is not None else s.get('default', '—'))}</code>" + (f"  {ui.escape(s.get('description', ''))}" if s.get("description") else ""))
            return await ctx.reply(event, ui.panel(f"TG-Radar · {ui.escape(name)} 配置", [ui.section("配置项", rows)]), prefer_edit=False)
        if len(parts) < 3:
            return await ctx.reply(event, ui.panel("TG-Radar · 参数不足", [ui.section("用法", [f"<code>{app.config.cmd_prefix}pluginconfig {name} 键 值</code>"])]), prefer_edit=False)
        key, raw = parts[1], parts[2]
        if raw.lower() in ("true", "yes", "on"): value = True
        elif raw.lower() in ("false", "no", "off"): value = False
        else:
            try: value = int(raw)
            except ValueError:
                try: value = float(raw)
                except ValueError: value = raw
        pcfg.set(key, value)
        await ctx.reply(event, ui.panel("TG-Radar · 配置已更新", [ui.section("详情", [ui.bullet("插件", name), ui.bullet(key, str(value))])]), prefer_edit=False)
