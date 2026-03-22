"""
TG-Radar Plugin SDK
====================

Every plugin should ONLY import from this module::

    from tgr.plugin_sdk import PluginContext

This is the stable API boundary between core and plugins.
Core internals may change freely — this SDK will remain backward-compatible.

Available through PluginContext (ctx):
    ctx.config          Plugin's own config file (configs/name.json)
    ctx.db              Whitelisted database methods
    ctx.ui              HTML rendering toolkit
    ctx.bus             Command bus for submitting background jobs
    ctx.log             Per-plugin logger (plugin.name)
    ctx.client          Telethon client (read-only)
    ctx.event           Event bus for inter-plugin communication
    ctx.app             Full app reference (use sparingly)

    ctx.command()       Decorator: register admin command
    ctx.hook()          Decorator: register core message hook
    ctx.on()            Decorator: subscribe to event bus
    ctx.emit()          Publish event
    ctx.cleanup()       Decorator: register unload cleanup
    ctx.healthcheck()   Decorator: register health check
    ctx.reply()         Reply to a message
"""
from __future__ import annotations

# Re-export PluginContext as the primary interface
from .core.plugin_system import PluginContext

# Re-export commonly used types for type hints in plugins
from .core.plugin_system import PluginRecord, CommandSpec, HookSpec

# Re-export telegram_utils items that plugins commonly need directly
from .telegram_utils import (
    RuleHit,
    collect_rule_hits,
    display_sender_name,
    render_alert_message,
    build_message_link,
    normalize_pattern_from_terms,
    merge_patterns,
    split_terms,
    try_remove_terms_from_pattern,
)

__all__ = [
    "PluginContext",
    "PluginRecord",
    "CommandSpec",
    "HookSpec",
    "RuleHit",
    "collect_rule_hits",
    "display_sender_name",
    "render_alert_message",
    "build_message_link",
    "normalize_pattern_from_terms",
    "merge_patterns",
    "split_terms",
    "try_remove_terms_from_pattern",
]
