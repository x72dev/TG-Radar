<div align="center">

<img src="https://readme-typing-svg.herokuapp.com?font=Fira+Code&weight=800&size=36&pause=1000&color=00D2FF&center=true&vCenter=true&width=800&lines=⚡+TG-Radar+;Precision+Keyword+Intelligence;Simplified+Deployment+Experience" alt="TG-Radar" />

**一款为 Telegram 深度定制的工业级关键词实时感知系统**

<p align="center">
  <img src="https://img.shields.io/badge/Deploy-One--Step-00D2FF?style=for-the-badge&logo=rocket" />
  <img src="https://img.shields.io/badge/OS-Linux--Server-white?style=for-the-badge&logo=linux" />
  <img src="https://img.shields.io/badge/Auth-Session--Base-blue?style=for-the-badge&logo=telegram" />
</p>

</div>

---

## 🚀 极速部署
> [!IMPORTANT]
> **复制下方命令到你的终端，剩下的交给我。**
> 脚本会自动完成：虚拟环境配置、依赖安装、Service 服务注册、`TGR` 快捷键映射。

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)
```

---

## 🛠️ TGR 极简控制台
安装完成后，在终端输入 **`TGR`**。这不再是一个冷冰冰的脚本，而是你的**图形化运维管家**。

| 菜单选项 | 业务逻辑解析 |
| :--- | :--- |
| **[1] 一键部署** | 自动拉取最新的 `tg_monitor.py` 与 `sync_engine.py` 并初始化。 |
| **[4] 重启服务** | 毫秒级重载配置，不丢失已有的 Telegram Session 授权。 |
| **[5] 运行状态** | 实时调用 `journalctl` 抓取最近 20 行日志，所见即所得。 |
| **[6] 重新授权** | 解决 Session 失效的终极方案，直接通过管理面板重走验证流程。 |
| **[7] 完全卸载** | 优雅退出，自动清理残留的 Systemd 服务与 Cron 定时任务。 |

---

## 💬 ChatOps 交互（核心玩法）
**这是 TG-Radar 最顶尖的交互设计。** 你无需再回 SSH 敲代码，所有操作都在 Telegram 聊天窗口完成。

### 📡 态势感知指令
- `-ping` —— **心跳自检**：返回系统已持续运行天数及当前负载。
- `-status` —— **全局图谱**：列出当前所有“活跃中”的分组及命中次数。
- `-log [行数]` —— **远程日志**：想看服务器发生什么了？发个消息，它就回传给你。

### ⚙️ 规则管理指令
- `-addrule [分组名]|[规则名]|[正则表达式]`
  > **示例：** `-addrule 搞机|苹果监控|iPhone\s15`
  > 只要有人在“搞机”分组发了包含 iPhone 15 的消息，秒级推送到你手机。
- `-sync`
  > **动态同步**：你在 TG 里新拉了群或改了分组名？发这条指令，`sync_engine` 自动对齐。

---

## 💎 核心黑科技：为什么它更稳？

### 1. 动态无感升级 (deploy.sh)
每次启动 `TGR` 时，后台会自动执行 `git fetch`。一旦发现开发者发布了新功能或修复了 Bug，面板会**高亮提示**。点击更新，代码自动覆写，你的配置完全保留。

### 2. 智能拓扑追踪 (sync_engine.py)
传统的监听脚本需要你手动填 Channel ID。TG-Radar 会**自动读取你的文件夹（Folder）**。你在 TG 客户端里把群拖进哪个文件夹，它就自动监听哪个文件夹。

### 3. 异步高并发 (tg_monitor.py)
基于 **Telethon** 纯异步框架设计。哪怕你同时监听几百个千人群，每秒上千条消息，系统也能在**不阻塞、不掉线**的前提下完成正则匹配。

---

## 🏥 极客维护手册

> [!TIP]
> **遇到问题？先看这里。**

- **手动查阅日志：** `journalctl -u tg_monitor -f`
- **配置文件路径：** `/root/TG-Radar/config.json`
- **Session 凭证：** `/root/TG-Radar/tg_radar.session` (请妥善保管)

---

<div align="center">

**TG-Radar — 专注于情报价值，而非复杂的配置。**

</div>
