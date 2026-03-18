<div align="center">

<img width="100%" src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=180&section=header&text=TG-Radar&fontSize=72&fontColor=fff&animation=twinkling&fontAlignY=32&desc=Telegram+Keyword+Intelligence+Monitor&descSize=18&descColor=rgba(255,255,255,0.8)&descAlignY=55"/>

<br/>

<a href="https://github.com/chenmo8848/TG-Radar/releases"><img src="https://img.shields.io/github/v/release/chenmo8848/TG-Radar?style=for-the-badge&logo=github&color=0d1117&labelColor=161b22&label=Latest"/></a>
&nbsp;
<img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white&labelColor=161b22"/>
&nbsp;
<img src="https://img.shields.io/badge/Telegram-MTProto-26A5E4?style=for-the-badge&logo=telegram&logoColor=white&labelColor=161b22"/>
&nbsp;
<img src="https://img.shields.io/badge/Platform-Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=161b22"/>
&nbsp;
<img src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge&labelColor=161b22"/>

<br/><br/>

**实时监控 Telegram 群组关键词，命中即推送告警。**  
**部署一次，此后全靠手机管理，永不需要再登服务器。**

<br/>

[快速安装](#-快速安装) · [使用流程](#-使用流程) · [ChatOps 指令](#-chatops-指令) · [配置](#-配置文件)

</div>

---

## ✦ 核心特性

<table>
<tr>
<td width="50%" valign="top">

**🎯 实时关键词监控**  
正则表达式规则引擎，支持多分组同时监控，物理拦截 Bot 消息，命中延迟 < 1s。

**📱 ChatOps 管理**  
在告警频道或 Saved Messages 发送指令即可管理。指令触发后消息**原地编辑**为结果，聊天界面保持整洁。

</td>
<td width="50%" valign="top">

**🔄 自愈同步引擎**  
定时从 Telegram 云端拉取分组结构，自动处理改名 / 新增 / 删除，联动更新规则名称。

**🔔 智能更新检测**  
每次执行 `TGR` 时静默检查版本。有新版本直接进入更新引导；已是最新则无任何额外交互，直接进菜单。

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

```
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

- **阶段二** 自动连接 Telegram，列出所有分组和频道，引导选择
- 分组、群组 ID、告警频道 **全部自动写入** `config.json` 和 `_system_cache`
- 无需任何手动查询 ID

---

## 💬 ChatOps 指令

在**告警频道**或 **Saved Messages** 发送指令。  
默认前缀 `-`，可在 `config.json → cmd_prefix` 修改。

> **edit-in-place**：指令触发后原地编辑为 ⏳，处理完成后再次编辑为最终结果。

<details open>
<summary><b>📊 查询</b></summary>

| 指令 | 说明 |
|------|------|
| `-help` | 指令菜单 |
| `-ping` | 心跳 · 在线时长 · 累计命中 |
| `-status` | 状态报告（群组 / 规则 / 分组 / 命中） |
| `-log [n]` | 系统日志，默认 20 行，最多 100 |
| `-folders` | 所有分组概览 |
| `-rules <分组名>` | 指定分组的规则列表 |

</details>

<details>
<summary><b>⚙️ 配置（自动重启生效）</b></summary>

| 指令 | 说明 |
|------|------|
| `-enable <分组名>` | 启用分组监控 |
| `-disable <分组名>` | 停止分组监控 |
| `-addrule <分组>\|<规则名>\|<正则>` | 添加规则（自动验证正则语法） |
| `-delrule <分组>\|<规则名>` | 删除规则 |
| `-setalert <分组>\|<频道ID>` | 设置分组专属告警频道 |
| `-setglobal <频道ID>` | 更新全局告警频道 |

</details>

<details>
<summary><b>🔧 系统</b></summary>

| 指令 | 说明 |
|------|------|
| `-sync` | 立即触发云端分组同步 |
| `-restart` | 重启监控服务 |

</details>

> 分组名支持模糊匹配（大小写不敏感 + 子串匹配），无需精确输入。

---

## 📨 告警卡片

```
🎯  规则名称
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 分组  ·  商机监控
📍 来源  ·  某房产交流群
👤 用户  ·  @username
🕐 时间  ·  14:32:05
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💬 消息内容
有套房子急售，280万，随时看房...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔗 直达消息
```

---

## 📬 消息路由

| 消息类型 | 推送目标 |
|---------|---------|
| 🚀 上线通知 | Saved Messages |
| 🔄 同步报告 | Saved Messages |
| 🎯 告警推送 | 各分组配置的告警频道 |
| 💬 ChatOps 回复 | 原地编辑发出指令的消息 |

---

## ⚙️ 配置文件

`/root/TG-Radar/config.json`

```jsonc
{
    "api_id"   : 123456,                      // my.telegram.org
    "api_hash" : "xxxxxxxxxxxxxxxxxxxxxxxx",   // my.telegram.org
    "global_alert_channel_id" : -100123456789, // 全局告警频道
    "notify_channel_id"       : null,          // 系统通知（null = 同告警频道）
    "cmd_prefix"              : "-",           // 指令前缀
    "folder_rules"  : {},                      // 自动维护
    "_system_cache" : {}                       // 自动维护
}
```

---

## 🗂️ 目录结构

```
/root/TG-Radar/
├── tg_monitor.py         核心守护进程
├── sync_engine.py        自愈同步引擎
├── config.json           配置文件
├── deploy.sh             管理脚本 (TGR)
├── TG_Radar_session.*    登录凭证 (auto)
└── venv/                 Python 环境 (auto)

/usr/local/bin/TGR                       全局命令
/etc/systemd/system/tg_monitor.service   系统服务
```

---

## 🗑️ 卸载

```bash
TGR  # 选择 7  完全卸载
```

---

<div align="center">

如果这个项目对你有帮助，欢迎点个 **Star** ⭐

<a href="https://github.com/chenmo8848/TG-Radar/stargazers"><img src="https://img.shields.io/github/stars/chenmo8848/TG-Radar?style=social"/></a>

<img width="100%" src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=100&section=footer"/>

</div>
