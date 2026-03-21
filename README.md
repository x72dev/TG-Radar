<img src="https://capsule-render.vercel.app/api?type=rounded&height=220&color=0:EDEDED,45:CBD5E1,100:64748B&text=TG-Radar&fontSize=50&fontColor=111827&fontAlignY=40&desc=Modern%20Telegram%20Radar%20for%20Sync%20Routing%20and%20Live%20Monitoring&descAlignY=63" width="100%" />

<div align="center">

<img src="https://readme-typing-svg.herokuapp.com?font=Inter&weight=600&size=20&duration=2800&pause=700&color=111827&center=true&vCenter=true&width=980&lines=Plan+C+%C2%B7+Admin%2FCore+%E5%8F%8C%E6%9C%8D%E5%8A%A1+%C2%B7+SQLite+WAL;%E8%87%AA%E5%8A%A8%E5%90%8C%E6%AD%A5+%C2%B7+%E7%83%AD%E6%9B%B4%E6%96%B0+%C2%B7+Saved+Messages+ChatOps;%E5%85%B3%E9%94%AE%E4%BA%8B%E4%BB%B6%E6%97%A5%E5%BF%97+%C2%B7+%E8%81%9A%E5%90%88%E5%91%8A%E8%AD%A6+%C2%B7+%E7%9F%AD%E8%A1%8C%E5%8D%A1%E7%89%87;%E4%B8%80%E6%9D%A1%E5%91%BD%E4%BB%A4%E9%83%A8%E7%BD%B2%E5%88%B0+%2Froot%2FTG-Radar" alt="typing" />

<p>
  <img src="https://img.shields.io/badge/Architecture-Admin%20%2B%20Core-111827?style=for-the-badge" alt="Architecture" />
  <img src="https://img.shields.io/badge/Storage-SQLite%20WAL-334155?style=for-the-badge" alt="Storage" />
  <img src="https://img.shields.io/badge/Command-TR-475569?style=for-the-badge" alt="TR" />
  <img src="https://img.shields.io/badge/Deploy-/root/TG--Radar-64748B?style=for-the-badge" alt="Deploy" />
</p>

</div>

---

## 项目概览

**TG-Radar** 是一套面向 Telegram 个人号场景的现代化雷达系统，核心围绕三条主线：

- **分组拓扑低频维护同步**
- **关键词实时监听与事件驱动热更新**
- **Saved Messages 控制台式交互**

这一版保留原项目的实战逻辑，并将底层整理为更稳定的 **Plan C**：

- **Admin Service**：负责收藏夹交互、后台调度、同步、自动收纳、更新与重启
- **Core Service**：负责实时监听、规则匹配、告警发送
- **SQLite WAL**：负责状态共享、任务持久化、配置快照兼容

---

## 核心能力

| 模块 | 说明 |
|---|---|
| 自动同步 | 支持每日错峰同步、手动同步、事件驱动热更新 |
| Telegram 交互 | 在 `Saved Messages` 中完整管理分组、规则、路由、同步、更新、重启 |
| 告警体验 | 同一目标聚合告警、重复命中计数、原消息直达链接 |
| 关键事件日志 | `-log` 默认只展示关键事件，去噪、中文化、卡片化 |
| 自动收纳 | 支持每日错峰扫描与手动扫描，不和前台交互抢链路 |
| 长期运行 | systemd 双服务、SQLite WAL、持久化队列 |

---

## 一键安装

> 默认部署目录：`/root/TG-Radar`

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)
```

安装向导会自动完成：

1. 系统依赖安装
2. Python 虚拟环境初始化
3. `config.json` 生成
4. Telegram 首次授权
5. systemd 双服务注册
6. 首次同步
7. 服务启动

---

## 终端控制

部署完成后直接使用：

```bash
TR
TR status
TR doctor
TR sync
TR reauth
TR logs admin
TR logs core
TR update
TR uninstall
```

---

## Telegram 控制台

在 **Saved Messages / 收藏夹** 中发送命令。

```text
-help
-status
-folders
-rules 示例分组
-enable 示例分组
-addrule 示例分组 规则A 监控词A 监控词B
-setrule 示例分组 规则A 新表达式
-delrule 示例分组 规则A
-delrule 示例分组 规则A 监控词A
-addroute 示例分组 标题词A 标题词B
-routescan
-jobs
-sync
-update
-restart
```

### 规则写法

- 空格、英文逗号、中文逗号都会被识别为分隔符
- 多个普通词会自动合并为 **OR 规则**
- 单个正则表达式会按原样使用
- 同名规则默认 **追加** 新词；需要整体覆盖时请使用 `-setrule`
- 分组名、规则名、短语关键词如包含空格，请用引号包起来

### 交互特性

- **优先编辑原命令消息**，减少控制台刷屏
- 帮助面板、状态面板、同步结果属于**临时面板**，可按配置自动回收
- **关键词告警与系统通知默认保留**，不会自动回收
- 分组启停、规则变更、缓存变动会通过 **事件驱动 reload** 即时生效

---

## 目录结构

```text
TG-Radar/
├─ install.sh
├─ deploy.sh
├─ config.example.json
├─ requirements.txt
└─ src/
   ├─ radar_admin.py
   ├─ radar_core.py
   ├─ bootstrap_session.py
   ├─ sync_once.py
   └─ tgr/
      ├─ admin_service.py
      ├─ core_service.py
      ├─ scheduler.py
      ├─ sync_logic.py
      ├─ db.py
      ├─ config.py
      └─ telegram_utils.py
```

---

## 日志说明

- Telegram 里的 `-log` 默认展示**关键事件**
- `-log all 20` 可查看更完整的事件流
- 终端完整日志请使用：

```bash
TR logs admin
TR logs core
```

---

## 卸载

彻底卸载：

```bash
TR uninstall
```

只卸服务和命令，保留项目目录：

```bash
TR uninstall keep-data
```

清理旧版残留：

```bash
TR cleanup-legacy
```

---

## 说明

- 首次 Telegram 登录仍然需要输入 **手机号 / 验证码 / 二步密码（如已开启）**
- `runtime/` 中的日志、session、数据库文件都属于运行时数据，不建议提交到 GitHub
- 上传仓库时不要提交真实 session 与数据库


## Admin 三层调度架构

- **命令层**：接收 Telegram 命令并快速回执。
- **调度层**：统一延迟、合并、串行 sync / route / update / restart / snapshot flush。
- **执行层**：后台执行重任务，尽量不阻塞命令响应。

## 配置说明

- 运行时使用纯净的 `config.json`。
- 字段中文说明、类型、默认值、示例位于 `config.schema.json`。
- 旧版写入的中文说明字段会在下次保存时自动清理。
