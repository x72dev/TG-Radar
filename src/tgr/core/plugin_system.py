from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

CommandHandler = Callable[..., Awaitable[None]]
MessageHook = Callable[..., Awaitable[None]]
HealthCheck = Callable[..., Any]
CleanupFunc = Callable[[], Any]
EventHandler = Callable[..., Any]

_log = logging.getLogger("tgr.plugin_system")


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
    event_handlers: list[tuple[str, EventHandler]] = field(default_factory=list)
    run_count: int = 0
    fail_count: int = 0
    fuse_count: int = 0
    fuse_threshold: int = 5
    last_error: str | None = None
    last_run_at: str | None = None
    last_health: str = "unknown"
    last_health_detail: str = "尚未检测"
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


# ── Registries ──

class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        for key in [spec.name.lower(), *(a.lower() for a in spec.aliases)]:
            self._commands[key] = spec

    def unregister_by_plugin(self, plugin_name: str) -> None:
        self._commands = {k: v for k, v in self._commands.items() if v.plugin_name != plugin_name}

    def get(self, name: str) -> CommandSpec | None:
        return self._commands.get(name.lower())

    def all(self) -> list[CommandSpec]:
        seen: set[tuple[str, str]] = set()
        result: list[CommandSpec] = []
        for spec in self._commands.values():
            key = (spec.plugin_name, spec.name)
            if key not in seen:
                seen.add(key)
                result.append(spec)
        result.sort(key=lambda s: (s.category, s.name))
        return result


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: list[HookSpec] = []

    def register(self, spec: HookSpec) -> None:
        self._hooks.append(spec)
        self._hooks.sort(key=lambda h: (h.order, h.name))

    def unregister_by_plugin(self, plugin_name: str) -> None:
        self._hooks = [h for h in self._hooks if h.plugin_name != plugin_name]

    def all(self) -> list[HookSpec]:
        return list(self._hooks)


# ── Event Bus ──

class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[str, EventHandler]]] = {}

    def subscribe(self, event: str, plugin_name: str, handler: EventHandler) -> None:
        self._handlers.setdefault(event, []).append((plugin_name, handler))

    def unsubscribe_plugin(self, plugin_name: str) -> None:
        for event in list(self._handlers):
            self._handlers[event] = [(pn, h) for pn, h in self._handlers[event] if pn != plugin_name]
            if not self._handlers[event]:
                del self._handlers[event]

    async def emit(self, event: str, data: Any = None) -> None:
        for plugin_name, handler in self._handlers.get(event, []):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                _log.warning("event handler error: plugin=%s event=%s: %s", plugin_name, event, exc)


# ── File-based Plugin Config ──

class PluginConfigFile:
    def __init__(self, configs_dir: Path, plugin_name: str, schema: dict[str, Any]) -> None:
        self._path = configs_dir / f"{plugin_name}.json"
        self._schema = schema
        self._plugin_name = plugin_name
        self._data: dict[str, Any] = {}
        self._load()

    def _defaults(self) -> dict[str, Any]:
        return {k: v.get("default") for k, v in self._schema.items() if "default" in v}

    def _load(self) -> None:
        defaults = self._defaults()
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._data = {**defaults, **{k: v for k, v in raw.items() if not k.startswith("_")}}
            except Exception:
                self._data = dict(defaults)
        else:
            self._data = dict(defaults)
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"_plugin": self._plugin_name}
        payload.update(self._data)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        schema_entry = self._schema.get(key, {})
        return schema_entry.get("default", default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def all(self) -> dict[str, Any]:
        return dict(self._data)

    def schema(self) -> dict[str, Any]:
        return dict(self._schema)


# ── PluginContext ──

class PluginContext:
    def __init__(self, manager: PluginManager, record: PluginRecord) -> None:
        self._manager = manager
        self._record = record
        self._app = manager.app
        from .._plugin_exports import PluginDB, PluginUI, PluginBus
        self.db: PluginDB = PluginDB(manager.app)
        self.ui: PluginUI = PluginUI()
        self.bus: PluginBus = PluginBus(manager.app)
        self.config: PluginConfigFile = manager.get_plugin_config_file(record.name, record.config_schema)
        self.log: logging.Logger = manager.get_plugin_logger(record.name)
        self.event: EventBus = manager.event_bus

    @property
    def client(self):
        return getattr(self._app, "client", None)

    @property
    def plugin_name(self) -> str:
        return self._record.name

    @property
    def app(self):
        return self._app

    def command(self, name, *, summary, usage, category="通用", aliases=(), heavy=False, hidden=False):
        def decorator(func):
            record = self._record
            async def wrapped(app, event, args):
                if record.is_fused:
                    return
                try:
                    await func(app, event, args)
                    record.mark_success()
                    if self._manager.db:
                        self._manager.db.reset_plugin_fuse(record.name)
                except Exception as exc:
                    record.mark_failure(exc)
                    self._check_fuse()
                    raise
            spec = CommandSpec(name=name, handler=wrapped, plugin_name=record.name, summary=summary, usage=usage, category=category, aliases=aliases, heavy=heavy, hidden=hidden)
            self._manager.command_registry.register(spec)
            record.commands.append(name)
            return func
        return decorator

    def hook(self, name, *, summary, order=100):
        def decorator(func):
            record = self._record
            async def wrapped(app, event):
                if record.is_fused:
                    return
                try:
                    await func(app, event)
                    record.mark_success()
                    if self._manager.db:
                        self._manager.db.reset_plugin_fuse(record.name)
                except Exception as exc:
                    record.mark_failure(exc)
                    self._check_fuse()
                    raise
            spec = HookSpec(name=name, handler=wrapped, plugin_name=record.name, summary=summary, order=order)
            self._manager.hook_registry.register(spec)
            record.hooks.append(name)
            return func
        return decorator

    def on(self, event_name):
        def decorator(func):
            self._manager.event_bus.subscribe(event_name, self._record.name, func)
            self._record.event_handlers.append((event_name, func))
            return func
        return decorator

    async def emit(self, event_name, data=None):
        await self._manager.event_bus.emit(event_name, data)

    def cleanup(self, func):
        self._record.cleanups.append(func)
        return func

    def healthcheck(self, func):
        self._record.healthcheck = func
        return func

    async def reply(self, event, text, **kwargs):
        if hasattr(self._app, "safe_reply"):
            await self._app.safe_reply(event, text, **kwargs)

    # Legacy compat
    def register_command(self, name, handler, **kw):
        self.command(name, **kw)(handler)

    def register_message_hook(self, name, handler, **kw):
        self.hook(name, **kw)(handler)

    def register_cleanup(self, func):
        self.cleanup(func)

    def set_healthcheck(self, func):
        self.healthcheck(func)

    def _check_fuse(self):
        rec = self._record
        if rec.is_fused and self._manager.db:
            self._manager.db.set_plugin_enabled(rec.name, False)
            self._manager.db.log_event("ERROR", "PLUGIN_FUSE", f"{rec.name} 连续失败 {rec.fuse_count} 次，已自动停用")
            _log.error("plugin %s fused after %d failures", rec.name, rec.fuse_count)


# ── PluginManager ──

class PluginManager:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.command_registry = CommandRegistry()
        self.hook_registry = HookRegistry()
        self.event_bus = EventBus()
        self.plugins: dict[str, PluginRecord] = {}
        self.load_errors: list[str] = []
        self.db = getattr(app, "db", None)
        self._config_files: dict[str, PluginConfigFile] = {}
        self._loggers: dict[str, logging.Logger] = {}

    def _configs_dir(self) -> Path:
        return getattr(self.app.config, "configs_dir", Path("configs"))

    def _logs_dir(self) -> Path:
        return getattr(self.app.config, "logs_dir", Path("runtime/logs"))

    def get_plugin_config_file(self, name: str, schema: dict[str, Any] | None = None) -> PluginConfigFile:
        if name not in self._config_files:
            self._config_files[name] = PluginConfigFile(self._configs_dir(), name, schema or {})
        return self._config_files[name]

    def get_plugin_logger(self, name: str) -> logging.Logger:
        if name not in self._loggers:
            from ..logger import get_plugin_logger
            self._loggers[name] = get_plugin_logger(name, self._logs_dir())
        return self._loggers[name]

    def _builtin_root(self) -> Path:
        return Path(__file__).resolve().parent.parent / "builtin_plugins"

    def _external_root(self) -> Path:
        return getattr(self.app.config, "plugins_root")

    def _iter_plugin_files(self, root: Path, kind: str) -> list[Path]:
        target = root / kind
        if not target.exists():
            return []
        return sorted([p for p in target.rglob("*.py") if p.is_file() and p.name != "__init__.py"])

    def _record_load_issue(self, prefix: str, message: str, *, level: str = "error") -> str:
        entry = f"{prefix}: {message}"
        if entry not in self.load_errors:
            self.load_errors.append(entry)
        log_fn = getattr(_log, level, _log.error)
        log_fn(message)
        return entry

    def validate_external_plugins(self) -> list[str]:
        prefix = "external_plugins"
        self.load_errors = [e for e in self.load_errors if not e.startswith(f"{prefix}:")]
        root = self._external_root()
        if not root.exists():
            message = f"外部插件目录不存在或不可见：{root}。请检查 plugins_dir 配置、Docker 挂载或软链接目标。"
            self._record_load_issue(prefix, message)
            return [message]
        admin_files = self._iter_plugin_files(root, "admin")
        core_files = self._iter_plugin_files(root, "core")
        if admin_files or core_files:
            return []
        message = f"外部插件目录为空或不可见：{root}。请检查 plugins_dir 配置、Docker 挂载或软链接目标。"
        self._record_load_issue(prefix, message)
        return [message]

    def _load_single(self, file_path: Path, kind: str, source: str) -> PluginRecord:
        plugin_name = file_path.stem
        record = PluginRecord(name=plugin_name, kind=kind, source=source, path=str(file_path))
        if self.db:
            db_state = self.db.get_plugin_state(plugin_name)
            if db_state:
                record.enabled = bool(db_state["enabled"])
                record.fuse_count = int(db_state.get("fuse_count", 0))
                record.fuse_threshold = int(db_state.get("fuse_threshold", 5))
            else:
                self.db.upsert_plugin_state(plugin_name, kind, source, enabled=True)
        if not record.enabled:
            record.loaded = False
            record.last_health = "disabled"
            record.last_health_detail = "已手动停用"
            return record
        try:
            module_name = f"tgr_plugin_{kind}_{plugin_name}"
            if module_name in sys.modules:
                del sys.modules[module_name]
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"无法加载: {file_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            meta = getattr(module, "PLUGIN_META", None) or getattr(module, "META", None) or {}
            record.version = str(meta.get("version") or record.version)
            record.description = str(meta.get("description") or plugin_name)
            record.author = str(meta.get("author", ""))
            record.depends = list(meta.get("depends") or [])
            record.conflicts = list(meta.get("conflicts") or [])
            record.min_core_version = str(meta.get("min_core_version", ""))
            record.config_schema = dict(meta.get("config_schema") or {})
            for dep in record.depends:
                if dep not in self.plugins or not self.plugins[dep].loaded:
                    raise RuntimeError(f"缺少依赖: {dep}")
            for conflict in record.conflicts:
                if conflict in self.plugins and self.plugins[conflict].loaded:
                    raise RuntimeError(f"与 {conflict} 冲突")
            ctx = PluginContext(self, record)
            setup_fn = getattr(module, "setup", None) or getattr(module, "register", None)
            if setup_fn is None:
                raise RuntimeError("缺少 setup(ctx) 入口函数")
            result = setup_fn(ctx)
            if asyncio.iscoroutine(result):
                try:
                    asyncio.get_running_loop().create_task(result)
                except RuntimeError:
                    asyncio.get_event_loop().run_until_complete(result)
            record.loaded = True
            record.module = module
        except Exception as exc:
            record.load_error = str(exc)
            record.loaded = False
            self.load_errors.append(f"{kind}:{plugin_name}: {exc}")
            _log.exception("加载插件失败 %s: %s", plugin_name, exc)
        return record

    def _unload_single(self, plugin_name: str) -> bool:
        record = self.plugins.get(plugin_name)
        if record is None:
            return False
        for cleanup in record.cleanups:
            try:
                result = cleanup()
                if asyncio.iscoroutine(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        pass
            except Exception as exc:
                _log.warning("cleanup error %s: %s", plugin_name, exc)
        if record.module:
            td = getattr(record.module, "teardown", None)
            if td:
                try:
                    ctx = PluginContext(self, record)
                    result = td(ctx)
                    if asyncio.iscoroutine(result):
                        try:
                            asyncio.get_running_loop().create_task(result)
                        except RuntimeError:
                            pass
                except Exception as exc:
                    _log.warning("teardown error %s: %s", plugin_name, exc)
        self.command_registry.unregister_by_plugin(plugin_name)
        self.hook_registry.unregister_by_plugin(plugin_name)
        self.event_bus.unsubscribe_plugin(plugin_name)
        for key in [k for k in sys.modules if k.startswith("tgr_plugin_") and plugin_name in k]:
            del sys.modules[key]
        record.loaded = False
        record.commands.clear()
        record.hooks.clear()
        record.cleanups.clear()
        record.event_handlers.clear()
        record.module = None
        record.load_error = None
        return True

    def _load_from_dir(self, root: Path, kind: str, source: str) -> None:
        if not root.exists():
            return
        for fp in self._iter_plugin_files(root, kind):
            record = self._load_single(fp, kind, source)
            self.plugins[record.name] = record

    def load_admin_plugins(self) -> None:
        for name in [n for n, r in self.plugins.items() if r.kind == "admin"]:
            self._unload_single(name)
            del self.plugins[name]
        self.load_errors = [e for e in self.load_errors if not e.startswith("admin:")]
        self._load_from_dir(self._builtin_root(), "admin", "builtin")
        self._load_from_dir(self._external_root(), "admin", "external")

    def load_core_plugins(self) -> None:
        for name in [n for n, r in self.plugins.items() if r.kind == "core"]:
            self._unload_single(name)
            del self.plugins[name]
        self.load_errors = [e for e in self.load_errors if not e.startswith("core:")]
        self._load_from_dir(self._builtin_root(), "core", "builtin")
        self._load_from_dir(self._external_root(), "core", "external")

    def reload_plugin(self, name: str) -> tuple[bool, str]:
        record = self.plugins.get(name)
        if record is None:
            return False, f"找不到插件 {name}"
        fp = Path(record.path)
        if not fp.exists():
            return False, f"文件不存在: {fp}"
        kind, source = record.kind, record.source
        self._unload_single(name)
        if self.db:
            self.db.set_plugin_enabled(name, True)
        self._config_files.pop(name, None)
        new = self._load_single(fp, kind, source)
        self.plugins[name] = new
        return (True, "重载成功") if new.loaded else (False, f"加载失败: {new.load_error}")

    def enable_plugin(self, name: str) -> tuple[bool, str]:
        record = self.plugins.get(name)
        if record is None:
            return False, f"找不到插件 {name}"
        if record.loaded and record.enabled:
            return True, "已在运行中"
        if self.db:
            self.db.set_plugin_enabled(name, True)
        record.enabled = True
        record.fuse_count = 0
        fp = Path(record.path)
        if not fp.exists():
            return False, "文件不存在"
        self._unload_single(name)
        new = self._load_single(fp, record.kind, record.source)
        self.plugins[name] = new
        return (True, "已启用") if new.loaded else (False, f"启用失败: {new.load_error}")

    def disable_plugin(self, name: str) -> tuple[bool, str]:
        record = self.plugins.get(name)
        if record is None:
            return False, f"找不到插件 {name}"
        if record.source == "builtin":
            return False, "内置插件不可停用"
        self._unload_single(name)
        record.enabled = False
        record.last_health = "disabled"
        record.last_health_detail = "已手动停用"
        if self.db:
            self.db.set_plugin_enabled(name, False)
        return True, "已停用"

    async def dispatch_admin_command(self, name: str, app, event, args: str) -> bool:
        spec = self.command_registry.get(name)
        if spec is None:
            return False
        await spec.handler(app, event, args)
        return True

    def is_heavy_command(self, name: str) -> bool:
        spec = self.command_registry.get(name)
        return bool(spec and spec.heavy)

    # PERF: parallel hook execution
    async def process_core_message(self, app, event) -> None:
        hooks = self.hook_registry.all()
        if not hooks:
            return
        if len(hooks) == 1:
            try:
                await hooks[0].handler(app, event)
            except Exception as exc:
                _log.exception("hook %s: %s", hooks[0].name, exc)
            return
        # Run hooks in parallel, each isolated
        async def _safe_run(hook: HookSpec):
            try:
                await hook.handler(app, event)
            except Exception as exc:
                _log.exception("hook %s: %s", hook.name, exc)
        await asyncio.gather(*[_safe_run(h) for h in hooks])

    def list_plugins(self, kind: str | None = None) -> list[PluginRecord]:
        return sorted([r for r in self.plugins.values() if kind is None or r.kind == kind], key=lambda r: (r.kind, r.name))

    def find_plugin(self, query: str) -> PluginRecord | None:
        if query in self.plugins:
            return self.plugins[query]
        lower = query.lower()
        for name, rec in self.plugins.items():
            if name.lower() == lower:
                return rec
        return None

    async def run_healthchecks(self) -> None:
        for rec in self.list_plugins():
            if not rec.loaded or not rec.enabled:
                rec.last_health = "disabled" if not rec.enabled else ("error" if rec.load_error else "unknown")
                rec.last_health_detail = rec.load_error or ("已停用" if not rec.enabled else "未加载")
                continue
            if rec.is_fused:
                rec.last_health = "fused"
                rec.last_health_detail = f"连续失败 {rec.fuse_count} 次"
                continue
            if rec.healthcheck is None:
                rec.last_health = "ok"
                rec.last_health_detail = "正常"
                continue
            try:
                result = rec.healthcheck(self.app)
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, tuple):
                    status, detail = result
                else:
                    status, detail = "ok", str(result)
                rec.last_health = str(status)
                rec.last_health_detail = str(detail)
            except Exception as exc:
                rec.last_health = "error"
                rec.last_health_detail = str(exc)
