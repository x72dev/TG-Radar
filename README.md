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

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&size=18&duration=3000&pause=1000&color=00D4FF&center=true&vCenter=true&multiline=true&width=600&height=80&lines=Real-time+Telegram+keyword+monitoring;Zero+SSH+after+first+deploy;Full+ChatOps+control+from+your+phone"/>
  <img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&size=18&duration=3000&pause=1000&color=00D4FF&center=true&vCenter=true&multiline=true&width=600&height=80&lines=Real-time+Telegram+keyword+monitoring;Zero+SSH+after+first+deploy;Full+ChatOps+control+from+your+phone"/>
</picture>

<br/><br/>

[**快速开始**](#-快速开始) &nbsp;·&nbsp; [**功能特性**](#-功能特性) &nbsp;·&nbsp; [**ChatOps**](#-chatops-指令参考) &nbsp;·&nbsp; [**配置**](#-配置参考) &nbsp;·&nbsp; [**卸载**](#-卸载)

</div>

<br/>

## 📡 项目简介

**TG-Radar** 是一款运行在 Linux 服务器上的 Telegram 群组关键词情报监控系统。

通过 Telegram 自身的 MTProto 协议接入，实时扫描指定群组和频道的消息流。关键词命中后立即推送结构化告警卡片，并附带消息直达链接。**首次部署完成后，所有后续操作均可通过 Telegram 指令远程完成，无需再次登录服务器。**

<br/>

## ✦ 功能特性

<table>
<tr>
<td valign="top" width="33%">

### 🎯 精准监控
- 正则表达式规则引擎
- 支持多分组同时监控
- 物理拦截 Bot 消息
- 命中即推送，延迟 < 1s

</td>
<td valign="top" width="33%">

### 📱 ChatOps 管理
- 无需 SSH，手机即可管理
- 告警频道直接发送指令
- 14 条完整指令覆盖全场景
- 分组名支持模糊匹配

</td>
<td valign="top" width="34%">

### 🔄 自愈同步
- 定时从云端拉取分组结构
- 自动处理改名 / 新增 / 删除
- 联动更新规则名称
- 原子写入防止配置损坏

</td>
</tr>
</table>

<br/>

## ⚡ 快速开始

### 一键安装

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)
```

> 需要 Linux root 权限。安装完成后自动注册全局命令 `TGR`。

<br/>

### 部署向导

```
$ TGR

  ╔══════════════════════════════════════════════════════╗
  ║         TG-Radar  —  Telegram 关键词监听雷达         ║
  ║                      v4.0.4                          ║
  ╚══════════════════════════════════════════════════════╝

  ●  监控服务    运行中
  ●  配置文件    已就绪
  ●  TGR 命令   已注册

   1   一键部署  （环境 + 配置 + 授权，全程引导）
   2   停止服务
   3   启动服务
   ...
```

选择 **`1`** 进入一键部署向导，三个阶段全程引导，无需手动操作：

```
阶段一  环境部署
  [1/5]  安装系统依赖 ........... 完成
  [2/5]  同步项目文件 ........... 完成
  [3/5]  配置 Python 虚拟环境 ... 完成
  [4/5]  注册 systemd 守护进程 .. 完成
  [5/5]  写入定时任务 & 注册 TGR  完成

阶段二  填写配置（交互式）
  ▸  输入 api_id 和 api_hash
  ▸  自动连接 Telegram，列出所有分组和频道
  ▸  选择监控分组 → 选择告警频道
  ▸  config.json + _system_cache 自动写入

阶段三  账号授权
  ▸  输入手机号和验证码完成登录
  ▸  分组数据自动同步
  ▸  监控服务自动启动
```

<br/>

## 💬 ChatOps 指令参考

在**告警频道**或 **Saved Messages** 中发送指令，雷达实时响应。

> 默认前缀 `-`，可在 `config.json` → `cmd_prefix` 修改为任意字符。

<details open>
<summary><b>📊 查询指令</b></summary>
<br/>

| 指令 | 说明 |
|------|------|
| `-help` | 显示完整指令菜单 |
| `-ping` | 心跳检测，返回在线时长和累计命中次数 |
| `-status` | 完整状态报告（运行时长 / 群数 / 规则数 / 最近命中） |
| `-log [n]` | 系统日志，默认 20 行，最多 100 行 |
| `-folders` | 所有分组概览（状态 / 群数 / 规则数 / 告警频道） |
| `-rules <分组名>` | 查看指定分组的完整规则列表 |

</details>

<details>
<summary><b>⚙️ 配置指令（自动重启生效）</b></summary>
<br/>

| 指令 | 说明 |
|------|------|
| `-enable <分组名>` | 启用分组监控 |
| `-disable <分组名>` | 停止分组监控 |
| `-addrule <分组>\|<规则名>\|<正则>` | 添加规则（写入前自动验证正则语法） |
| `-delrule <分组>\|<规则名>` | 删除规则 |
| `-setalert <分组>\|<频道ID>` | 为分组设置专属告警频道 |
| `-setglobal <频道ID>` | 更新全局默认告警频道 |

</details>

<details>
<summary><b>🔧 系统指令</b></summary>
<br/>

| 指令 | 说明 |
|------|------|
| `-sync` | 立即触发云端分组同步 |
| `-restart` | 重启监控服务 |

</details>

<br/>

## 📨 告警卡片

命中关键词后推送至告警频道，格式如下：

```
🎯  紧急商机
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

<br/>

## 📬 消息路由

| 消息类型 | 推送目标 |
|---------|---------|
| 🚀 上线通知 | Saved Messages |
| 🔄 同步报告 | Saved Messages |
| 🎯 告警推送 | 各分组配置的告警频道 |
| 💬 ChatOps 回复 | 发出指令所在的频道 |

<br/>

## ⚙️ 配置参考

**路径：** `/root/TG-Radar/config.json`

```jsonc
{
    "api_id"   : 123456,                      // my.telegram.org 获取
    "api_hash" : "xxxxxxxxxxxxxxxxxxxxxxxx",   // my.telegram.org 获取

    "global_alert_channel_id" : -100123456789, // 全局告警频道 ID
    "notify_channel_id"       : null,          // 系统通知频道（null = 同告警频道）
    "cmd_prefix"              : "-",           // ChatOps 指令前缀

    // 以下由系统自动维护 ───────────────────────────────────
    "folder_rules"  : {},
    "_system_cache" : {}
}
```

<br/>

## 🗂️ 项目结构

```
/root/TG-Radar/
├── tg_monitor.py         # 核心守护进程
├── sync_engine.py        # 自愈同步引擎
├── config.json           # 配置文件
├── deploy.sh             # 管理脚本 (TGR)
├── TG_Radar_session.*    # 登录凭证 (auto)
└── venv/                 # Python 虚拟环境 (auto)

/usr/local/bin/TGR                       # 全局快捷命令
/etc/systemd/system/tg_monitor.service   # 系统服务
```

<br/>

## 🗑️ 卸载

```bash
TGR
# 选择 7  完全卸载
```

可选是否保留 `/root/TG-Radar/` 目录，保留则再次部署时配置不丢失。

<br/>

---

<div align="center">

如果这个项目对你有帮助，欢迎点个 **Star** ⭐

<br/>

<a href="https://github.com/chenmo8848/TG-Radar/stargazers"><img src="https://img.shields.io/github/stars/chenmo8848/TG-Radar?style=social"/></a>
&nbsp;&nbsp;
<a href="https://github.com/chenmo8848/TG-Radar/issues"><img src="https://img.shields.io/github/issues/chenmo8848/TG-Radar?style=social"/></a>

<br/>

<img width="100%" src="https://capsule-render.vercel.app/api?type=waving&color=gradient&customColorList=6,11,20&height=100&section=footer"/>

</div>
