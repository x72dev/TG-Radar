from __future__ import annotations

from ..telegram_utils import bullet, escape, panel, section


def render_job_accept(command: str, trace: str, job_id: int) -> str:
    return panel(
        '任务已受理',
        [section('调度信息', [bullet('命令', command), bullet('任务 ID', job_id), bullet('跟踪号', trace, code=False)])],
        '<i>前台已完成受理；最终结果会单独回复，不再依赖编辑原命令消息。</i>',
    )


def render_help(prefix: str, specs) -> str:
    light = []
    heavy = []
    for spec in specs:
        line = f"<code>{escape(prefix)}{escape(spec.usage)}</code> — {escape(spec.summary)}"
        (heavy if spec.heavy else light).append(line)
    return panel('TG-Radar 控制台', [section('轻命令', light), section('后台任务', heavy)], '<i>轻命令直接回复；重任务先受理，再异步回包。</i>')
