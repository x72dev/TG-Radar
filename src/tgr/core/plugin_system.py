from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

CommandHandler = Callable[[Any, Any, str], Awaitable[None]]
MessageHook = Callable[[Any, Any], Awaitable[None]]
HealthCheck = Callable[[Any], Awaitable[tuple[str, str] | str] | tuple[str, str] | str]
CleanupFunc = Callable[[], Awaitable[None] | None]

logger = logging.getLogger("tgr.plugin_system")


@dataclass
class CommandSpec:
    name: str
    handler: CommandHandler
    plugin_name: str
    summary: str
    usage: str
    category: str = "通用"
    aliases: tuple[str, ...] = ()
    heavy: bool = False
    hidden: bool = False


@dataclass
class HookSpec:
    name: str
    handler: MessageHook
    plugin_name: str
    summary: str
    order: int = 100


@dataclass
class PluginRecord:
    name: str
    kind: str
    source: str
    path: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    depends: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    min_core_version: str = ""
    config_schema: dict[str, Any] = field(default_factory=dict)
    loaded: bool = False
    enabled: bool = True
    load_error: str | None = None
    commands: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    cleanups: list[CleanupFunc] = field(default_factory=list)
    run_count: int = 0
    fail_count: int = 0
    fuse_count: int = 0
    fuse_threshold: int = 5
    last_error: str | None = None
    last_run_at: str | None = None
    last_health: str = "unknown"
    last_health_detail: str = "未执行"
    healthcheck: HealthCheck | None = None
    module: Any = None

    def mark_success(self) -> None:
        self.run_count += 1
        self.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_error = None
        self.fuse_count = 0

    def mark_failure(self, exc: Exception) -> None:
        self.fail_count += 1
        self.fuse_count += 1
        self.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_error = str(exc)

    @property
    def is_fused(self) -> bool:
        return self.fuse_count >= self.fuse_threshold

    @property
    def state_label(self) -> str:
        if self.load_error:
            return "加载失败"
        if not self.enabled:
            return "已停用"
        if self.is_fused:
            return "已熔断"
        if self.loaded:
            return "运行中"
        return "未加载"


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    def clear(self) -> None:
        self._commands.clear()

    def register(self, spec: CommandSpec) -> None:
        keys = [spec.name.lower(), *[alias.lower() for alias in spec.aliases]]
        for key in keys:
            self._commands[key] = spec

    def unregister_by_plugin(self, plugin_name: str) -> None:
        to_remove = [key for key, spec in self._commands.items() if spec.plugin_name == plugin_name]
        for key in to_remove:
            del self._commands[key]

    def get(self, name: str) -> CommandSpec | None:
        return self._commands.get(name.lower())

    def all(self) -> list[CommandSpec]:
        seen: set[tuple[str, str]] = set()
        ordered: list[CommandSpec] = []
        for spec in self._commands.values():
            key = (spec.plugin_name, spec.name)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(spec)
        ordered.sort(key=lambda item: (item.category, item.name))
        return ordered


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: list[HookSpec] = []

    def clear(self) -> None:
        self._hooks.clear()

    def register(self, spec: HookSpec) -> None:
        self._hooks.append(spec)
        self._hooks.sort(key=lambda item: (item.order, item.name))

    def unregister_by_plugin(self, plugin_name: str) -> None:
        self._hooks = [h for h in self._hooks if h.plugin_name != plugin_name]

    def all(self) -> list[HookSpec]:
        return list(self._hooks)


class PluginContext:
    """Stable context handed to every plugin's setup() function."""

    def __init__(self, manager: PluginManager, plugin: PluginRecord, kind: str) -> None:
        self.manager = manager
        self.app = manager.app
        self.plugin = plugin
        self.kind = kind

    def register_command(
        self,
        name: str,
        handler: CommandHandler,
        *,
        summary: str,
        usage: str,
        category: str = "通用",
        aliases: tuple[str, ...] = (),
        heavy: bool = False,
        hidden: bool = False,
    ) -> None:
        record = self.plugin

        async def wrapped(app: Any, event: Any, args: str) -> None:
            if record.is_fused:
                return
            try:
                await handler(app, event, args)
                record.mark_success()
                if self.manager.db:
                    self.manager.db.reset_plugin_fuse(record.name)
            except Exception as exc:
                record.mark_failure(exc)
                self._check_fuse(record)
                raise

        spec = CommandSpec(
            name=name,
            handler=wrapped,
            plugin_name=self.plugin.name,
            summary=summary,
            usage=usage,
            category=category,
            aliases=aliases,
            heavy=heavy,
            hidden=hidden,
        )
        self.manager.command_registry.register(spec)
        self.plugin.commands.append(name)

    def register_message_hook(
        self,
        name: str,
        handler: MessageHook,
        *,
        summary: str,
        order: int = 100,
    ) -> None:
        record = self.plugin

        async def wrapped(app: Any, event: Any) -> None:
            if record.is_fused:
                return
            try:
                await handler(app, event)
                record.mark_success()
                if self.manager.db:
                    self.manager.db.reset_plugin_fuse(record.name)
            except Exception as exc:
                record.mark_failure(exc)
                self._check_fuse(record)
                raise

        spec = HookSpec(name=name, handler=wrapped, plugin_name=self.plugin.name, summary=summary, order=order)
        self.manager.hook_registry.register(spec)
        self.plugin.hooks.append(name)

    def register_cleanup(self, func: CleanupFunc) -> None:
        """Register a cleanup function called on unload/reload."""
        self.plugin.cleanups.append(func)

    def set_healthcheck(self, func: HealthCheck) -> None:
        self.plugin.healthcheck = func

    def get_config(self, key: str, default: Any = None) -> Any:
        """Read plugin-specific configuration value."""
        if self.manager.db:
            cfg = self.manager.db.get_plugin_config(self.plugin.name)
            if key in cfg:
                return cfg[key]
        schema = self.plugin.config_schema
        if key in schema and "default" in schema[key]:
            return schema[key]["default"]
        return default

    def _check_fuse(self, record: PluginRecord) -> None:
        if record.is_fused and self.manager.db:
            self.manager.db.set_plugin_enabled(record.name, False)
            self.manager.db.log_event("ERROR", "PLUGIN_FUSE", f"{record.name}: 连续失败 {record.fuse_count} 次，已自动停用")
            logger.error("plugin %s fused after %d consecutive failures", record.name, record.fuse_count)


class PluginManager:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.command_registry = CommandRegistry()
        self.hook_registry = HookRegistry()
        self.plugins: dict[str, PluginRecord] = {}
        self.load_errors: list[str] = []
        self.db = getattr(app, "db", None)

    def _module_name(self, base: str, file_path: Path) -> str:
        safe = "_".join(file_path.with_suffix("").parts[-3:])
        return f"tgr_dynamic_{base}_{safe}"

    def _iter_plugin_files(self, root: Path, kind: str) -> list[Path]:
        target = root / kind
        if not target.exists():
            return []
        return sorted([p for p in target.rglob("*.py") if p.is_file() and p.name != "__init__.py"])

    def _builtin_root(self) -> Path:
        return Path(__file__).resolve().parent.parent / "builtin_plugins"

    def _external_root(self) -> Path:
        return getattr(self.app.config, 'plugins_root')

    def _load_single(self, file_path: Path, kind: str, source: str) -> PluginRecord:
        """Load a single plugin from file. Returns the PluginRecord."""
        plugin_name = file_path.stem
        record = PluginRecord(name=plugin_name, kind=kind, source=source, path=str(file_path))

        # Check DB state for enable/disable
        if self.db:
            db_state = self.db.get_plugin_state(plugin_name)
            if db_state is not None:
                record.enabled = bool(db_state["enabled"])
                record.fuse_count = int(db_state.get("fuse_count", 0))
                record.fuse_threshold = int(db_state.get("fuse_threshold", 5))
            else:
                # First time seeing this plugin — register in DB
                self.db.upsert_plugin_state(plugin_name, kind, source, enabled=True)

        if not record.enabled:
            record.loaded = False
            record.last_health = "disabled"
            record.last_health_detail = "插件已被手动停用"
            return record

        try:
            module_name = self._module_name(kind, file_path)
            # Remove old module from sys.modules for clean reload
            if module_name in sys.modules:
                del sys.modules[module_name]

            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"无法加载插件文件: {file_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            meta = getattr(module, "PLUGIN_META", {}) or {}
            record.version = str(meta.get("version") or record.version)
            record.description = str(meta.get("description") or meta.get("display_name") or plugin_name)
            record.author = str(meta.get("author", ""))
            record.depends = list(meta.get("depends") or [])
            record.conflicts = list(meta.get("conflicts") or [])
            record.min_core_version = str(meta.get("min_core_version", ""))
            record.config_schema = dict(meta.get("config_schema") or {})

            # Validate dependencies
            for dep in record.depends:
                if dep not in self.plugins or not self.plugins[dep].loaded:
                    raise RuntimeError(f"依赖插件 '{dep}' 未加载")
            for conflict in record.conflicts:
                if conflict in self.plugins and self.plugins[conflict].loaded:
                    raise RuntimeError(f"与插件 '{conflict}' 冲突")

            ctx = PluginContext(self, record, kind)
            setup_fn = getattr(module, "setup", None) or getattr(module, "register", None)
            if setup_fn is None:
                raise RuntimeError("插件缺少 setup(ctx) 入口")

            result = setup_fn(ctx)
            if asyncio.iscoroutine(result):
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(result)
                else:
                    loop.run_until_complete(result)

            record.loaded = True
            record.module = module
        except Exception as exc:
            record.load_error = str(exc)
            record.loaded = False
            self.load_errors.append(f"{kind}:{plugin_name}: {exc}")
        return record

    def _unload_single(self, plugin_name: str) -> bool:
        """Unload a single plugin, running cleanup and removing registrations."""
        record = self.plugins.get(plugin_name)
        if record is None:
            return False

        # Run cleanup functions
        for cleanup in record.cleanups:
            try:
                result = cleanup()
                if asyncio.iscoroutine(result):
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(result)
                    else:
                        loop.run_until_complete(result)
            except Exception as exc:
                logger.warning("cleanup error for plugin %s: %s", plugin_name, exc)

        # Run teardown if exists
        if record.module is not None:
            teardown_fn = getattr(record.module, "teardown", None)
            if teardown_fn:
                try:
                    result = teardown_fn(PluginContext(self, record, record.kind))
                    if asyncio.iscoroutine(result):
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(result)
                        else:
                            loop.run_until_complete(result)
                except Exception as exc:
                    logger.warning("teardown error for plugin %s: %s", plugin_name, exc)

        # Remove registrations
        self.command_registry.unregister_by_plugin(plugin_name)
        self.hook_registry.unregister_by_plugin(plugin_name)

        # Clean module cache
        module_keys = [k for k in sys.modules if k.startswith(f"tgr_dynamic_") and plugin_name in k]
        for key in module_keys:
            del sys.modules[key]

        record.loaded = False
        record.commands.clear()
        record.hooks.clear()
        record.cleanups.clear()
        record.module = None
        record.load_error = None
        return True

    def _load_from_dir(self, root: Path, kind: str, source: str) -> None:
        if not root.exists():
            return
        for file_path in self._iter_plugin_files(root, kind):
            plugin_name = file_path.stem
            record = self._load_single(file_path, kind, source)
            self.plugins[plugin_name] = record

    def load_admin_plugins(self) -> None:
        # Unload existing admin plugins
        for name in list(self.plugins):
            if self.plugins[name].kind == "admin":
                self._unload_single(name)
                del self.plugins[name]
        self.load_errors = [e for e in self.load_errors if not e.startswith("admin:")]

        self._load_from_dir(self._builtin_root(), "admin", "builtin")
        self._load_from_dir(self._external_root(), "admin", "external")

    def load_core_plugins(self) -> None:
        # Unload existing core plugins
        for name in list(self.plugins):
            if self.plugins[name].kind == "core":
                self._unload_single(name)
                del self.plugins[name]
        self.load_errors = [e for e in self.load_errors if not e.startswith("core:")]

        self._load_from_dir(self._builtin_root(), "core", "builtin")
        self._load_from_dir(self._external_root(), "core", "external")

    def reload_plugin(self, name: str) -> tuple[bool, str]:
        """Reload a single plugin by name. Returns (success, message)."""
        record = self.plugins.get(name)
        if record is None:
            return False, f"插件 '{name}' 不存在"

        file_path = Path(record.path)
        if not file_path.exists():
            return False, f"插件文件不存在: {file_path}"

        kind = record.kind
        source = record.source

        # Unload
        self._unload_single(name)

        # Re-enable if was fused
        if self.db:
            self.db.set_plugin_enabled(name, True)

        # Load fresh
        new_record = self._load_single(file_path, kind, source)
        self.plugins[name] = new_record

        if new_record.loaded:
            return True, f"插件 '{name}' 已重新加载"
        return False, f"插件 '{name}' 加载失败: {new_record.load_error}"

    def enable_plugin(self, name: str) -> tuple[bool, str]:
        """Enable a plugin and load it."""
        record = self.plugins.get(name)
        if record is None:
            return False, f"插件 '{name}' 不存在"
        if record.loaded and record.enabled:
            return True, f"插件 '{name}' 已经在运行中"

        if self.db:
            self.db.set_plugin_enabled(name, True)
        record.enabled = True
        record.fuse_count = 0

        file_path = Path(record.path)
        if not file_path.exists():
            return False, f"插件文件不存在: {file_path}"

        # Reload it
        self._unload_single(name)
        new_record = self._load_single(file_path, record.kind, record.source)
        self.plugins[name] = new_record

        if new_record.loaded:
            return True, f"插件 '{name}' 已启用"
        return False, f"插件 '{name}' 启用失败: {new_record.load_error}"

    def disable_plugin(self, name: str) -> tuple[bool, str]:
        """Disable a plugin and unload it."""
        record = self.plugins.get(name)
        if record is None:
            return False, f"插件 '{name}' 不存在"

        if record.source == "builtin":
            return False, f"内置插件 '{name}' 不能停用"

        self._unload_single(name)
        record.enabled = False
        record.last_health = "disabled"
        record.last_health_detail = "插件已被手动停用"
        if self.db:
            self.db.set_plugin_enabled(name, False)
        return True, f"插件 '{name}' 已停用"

    async def dispatch_admin_command(self, name: str, app: Any, event: Any, args: str) -> bool:
        spec = self.command_registry.get(name)
        if spec is None:
            return False
        await spec.handler(app, event, args)
        return True

    def is_heavy_command(self, name: str) -> bool:
        spec = self.command_registry.get(name)
        return bool(spec.heavy) if spec else False

    async def process_core_message(self, app: Any, event: Any) -> None:
        for hook in self.hook_registry.all():
            try:
                await hook.handler(app, event)
            except Exception as exc:
                logger.exception("hook %s error: %s", hook.name, exc)

    def list_plugins(self, kind: str | None = None) -> list[PluginRecord]:
        records: list[PluginRecord] = []
        for record in self.plugins.values():
            if kind is not None and record.kind != kind:
                continue
            records.append(record)
        return sorted(records, key=lambda item: (item.kind, item.name))

    def find_plugin(self, query: str) -> PluginRecord | None:
        """Find plugin by exact name or case-insensitive match."""
        if query in self.plugins:
            return self.plugins[query]
        lower = query.lower()
        for name, record in self.plugins.items():
            if name.lower() == lower:
                return record
        return None

    async def run_healthchecks(self) -> None:
        for record in self.list_plugins():
            if not record.loaded or not record.enabled:
                if not record.enabled:
                    record.last_health = "disabled"
                    record.last_health_detail = "插件已停用"
                elif record.load_error:
                    record.last_health = "error"
                    record.last_health_detail = record.load_error
                else:
                    record.last_health = "unknown"
                    record.last_health_detail = "未加载"
                continue
            if record.is_fused:
                record.last_health = "fused"
                record.last_health_detail = f"连续失败 {record.fuse_count} 次，已自动停用"
                continue
            if record.healthcheck is None:
                record.last_health = "ok"
                record.last_health_detail = "未提供健康检查"
                continue
            try:
                result = record.healthcheck(self.app)
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, tuple):
                    status, detail = result
                else:
                    status, detail = "ok", str(result)
                record.last_health = str(status)
                record.last_health_detail = str(detail)
            except Exception as exc:
                record.last_health = "error"
                record.last_health_detail = str(exc)
                record.last_error = str(exc)
