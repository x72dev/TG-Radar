<div align="center">

<img width="100%" src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=180&section=header&text=TG-Radar&fontSize=72&fontColor=fff&animation=twinkling&fontAlignY=32&desc=Telegram+Keyword+Intelligence+Monitor&descSize=18&descColor=rgba(255,255,255,0.8)&descAlignY=55"/>

<br/>

<a href="https://github.com/chenmo8848/TG-Radar/releases"><img src="https://img.shields.io/github/v/release/chenmo8848/TG-Radar?style=for-the-badge&logo=github&color=0d1117&labelColor=161b22&label=v5.1.1"/></a>
&nbsp;
<img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white&labelColor=161b22"/>
&nbsp;
<img src="https://img.shields.io/badge/Telegram-MTProto-26A5E4?style=for-the-badge&logo=telegram&logoColor=white&labelColor=161b22"/>
&nbsp;
<img src="https://img.shields.io/badge/Platform-Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=161b22"/>
&nbsp;
<img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge&labelColor=161b22"/>

<br/><br/>

**实时监控 Telegram 群组关键词，命中即推送告警。** **部署一次，此后全靠手机管理，永不需要再登服务器。**

<br/>

[快速安装](#-快速安装) · [使用流程](#-使用流程) · [ChatOps 指令](#-chatops-指令) · [配置](#-配置文件)

</div>

---

## ✦ 核心特性

<table>
<tr>
<td width="50%" valign="top">

**🎯 实时关键词监控** 纯空格自然语言指令引擎，后台 AI 级自动去重、合并与剔除。物理拦截 Bot，命中延迟 < 1s。

**📱 ChatOps 极客管理** 在告警频道发送指令即可管理全站。支持官方最新 `<blockquote expandable>` 长文自动折叠排版，全中文极简 UI 态势大屏。

</td>
<td width="50%" valign="top">

**🔄 自愈同步引擎** 定时从 Telegram 云端无缝拉取分组结构，自动处理改名 / 新增 / 删除，全量热重载不断线。

**🔔 智能部署管家** 内置 `TGR` 全局管家。一键安装、静默版本更新比对、进程防波及重启、防正则注入保护，彻底解放运维。

</td>
</tr>
</table>

---

## ⚡ 快速安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)
```

> 需要 Linux root 权限。安装完成后自动注册全局命令 `TGR`。

---

## 📖 使用流程

```text
TGR
 │
 ├─ 检测到新版本 ──→  更新引导界面
 │                     1) 快速更新（保留配置，重启服务）
 │                     2) 完整重新部署（重走向导）
 │                     3) 跳过
 │
 └─ 已是最新版本 ──→  管理菜单（零额外交互）
                       1  一键部署   阶段一 环境 · 阶段二 配置 · 阶段三 授权
                       2  停止服务
                       3  启动服务
                       4  重启服务
                       5  状态与日志
                       6  重新授权
                       7  完全卸载
                       0  退出
```

**一键部署（选项 1）** 全程引导，无需手动操作：

- **阶段二** 自动连接 Telegram，列出所有分组和频道，引导选择。
- 管道分组、群组 ID、告警频道 **全部自动写入** `config.json` 和 `_system_cache`。
- 无需手动抓取任何繁琐的 ID。

---

## 💬 ChatOps 指令

在**告警频道**或 **Saved Messages** 发送指令。  
默认前缀 `-`，可在 `config.json → cmd_prefix` 修改。

> **edit-in-place**：指令触发后原地编辑为 ⏳ 处理中，完成后自动更新为最终结果大屏。

<details open>
<summary><b>📊 态势观测</b></summary>

| 指令 | 说明 |
|------|------|
| `-help` | 呼出核心控制台 |
| `-ping` | 节点心跳与存活探测 |
| `-status` | 全局监控态势大屏 |
| `-log [n]` | 提取系统核心运行日志 (支持超长折叠) |
| `-folders` | 检视数据管道拓扑矩阵 |
| `-rules <分组名>` | 查看指定管道的策略明细 |

</details>

<details>
<summary><b>🛡️ 策略引擎（纯空格传参，自动防注入）</b></summary>

| 指令 | 说明 |
|------|------|
| `-enable <分组名>` | 唤醒挂起的数据管道 |
| `-disable <分组名>` | 休眠活跃的数据管道 |
| `-addrule <分组> <规则> <词1> [词2]` | 智能策略挂载 (自动去重与正则合并) |
| `-delrule <分组> <规则> [词1]` | 精准策略剥离 (参数空则整体废弃) |
| `-setalert <分组> <频道ID>` | 分配独立告警路由 |
| `-setglobal <频道ID>` | 更新全局默认路由 |

</details>

<details>
<summary><b>⚙️ 系统底层</b></summary>

| 指令 | 说明 |
|------|------|
| `-sync` | 强制云端拓扑全量同步 |
| `-restart` | 热重启核心守护进程 |

</details>

---

## 📨 告警卡片交互

```text
🚨 [ 情报雷达告警 ]

🎯 触发关键词 : 卖房
🏷️ 命中策略 : 紧急 (商机监控)
📡 情报来源 : 某房产交流群
👤 发送载体 : @username
⏱ 捕获时间 : 14:32:05

[ 现场原始快照 ]
> 有套房子急售，280万，随时看房，全款还能谈...
> (此处支持长文自动折叠 Read more...)

🔗 [直达核心现场](https://t.me/c/1234567/8899)
```

---

## 📬 消息路由流向

| 消息类型 | 推送目标 |
|---------|---------|
| 🚀 引擎上线通知 | Saved Messages |
| 🔄 拓扑同步报告 | Saved Messages |
| 🚨 监控告警推送 | 各分组配置的独立告警频道 |
| 💬 ChatOps 回复 | 原地编辑发出指令的消息卡片 |

---

## ⚙️ 配置文件

`/root/TG-Radar/config.json`

> **注意**：标准 JSON 不支持 `//` 注释，程序使用 `_note_` 字段进行安全注记。

```json
{
    "api_id": 123456,
    "api_hash": "xxxxxxxxxxxxxxxxxxxxxxxx",
    "_note_channels": "下面是全局与系统告警频道ID",
    "global_alert_channel_id": -100123456789,
    "notify_channel_id": null,
    "cmd_prefix": "-",
    "folder_rules": {},
    "_system_cache": {}
}
```

---

## 🗂️ 目录结构

```text
/root/TG-Radar/
├── tg_monitor.py         核心守护进程
├── sync_engine.py        自愈同步引擎
├── config.json           配置文件 (动态生成)
├── deploy.sh             部署管家核心逻辑
├── TG_Radar_session.* 登录凭证 (安全隔离)
└── venv/                 Python 虚拟环境

/usr/local/bin/TGR                       系统级全局命令
/etc/systemd/system/tg_monitor.service   Systemd 守护服务
```

---

## 🗑️ 完全卸载

```bash
TGR  # 选择 7  完全卸载
```

---

<div align="center">

如果这个项目对你有帮助，欢迎点个 **Star** ⭐

<a href="https://github.com/chenmo8848/TG-Radar/stargazers"><img src="https://img.shields.io/github/stars/chenmo8848/TG-Radar?style=social"/></a>

<img width="100%" src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=100&section=footer"/>

</div>
