#!/bin/bash
# ============================================================
# TG-Radar 态势感知引擎 · 核心部署管家 v5.1.1
# ============================================================
set -e

INSTALL_DIR="/root/TG-Radar"
SERVICE_NAME="tg_monitor"

# 现代 CLI 色彩美学
B='\033[1m'
DIM='\033[2m'
RES='\033[0m'
MAIN='\033[36m'   # 青蓝色主轴
TEXT='\033[37m'   # 纯白文字

# 反色标签 (Background + Text) - 现代工具最流行的状态展现方式
TAG_OK='\033[42;30m'   # 绿底黑字
TAG_ERR='\033[41;37m'  # 红底白字
TAG_WARN='\033[43;30m' # 黄底黑字

_svc_active() { systemctl is-active --quiet $SERVICE_NAME; }
_svc_enabled() { systemctl is-enabled --quiet $SERVICE_NAME 2>/dev/null; }

show_menu() {
    clear
    echo -e "\n${MAIN}${B} ▌ TG-RADAR 核心控制台 ${DIM}v5.1.1${RES}"
    echo -e "${MAIN} │${RES}"

    # --- 状态树 ---
    echo -e "${MAIN} ├─ ${B}${TEXT}系统引擎状态${RES}"
    
    if _svc_active; then
        echo -e "${MAIN} │  ${DIM}守护进程    ${RES}${TAG_OK}  运行中  ${RES}"
    elif _svc_enabled; then
        echo -e "${MAIN} │  ${DIM}守护进程    ${RES}${TAG_WARN}  已挂起  ${RES} ${DIM} 已配置开机自启${RES}"
    else
        echo -e "${MAIN} │  ${DIM}守护进程    ${RES}${TAG_ERR}  未启动  ${RES}"
    fi

    if [ -f "$INSTALL_DIR/config.json" ]; then
        echo -e "${MAIN} │  ${DIM}核心配置    ${RES}${TAG_OK}  已就绪  ${RES}"
    else
        echo -e "${MAIN} │  ${DIM}核心配置    ${RES}${TAG_ERR}  已缺失  ${RES}"
    fi

    if [ -x "/usr/local/bin/TGR" ]; then
        echo -e "${MAIN} │  ${DIM}全局环境    ${RES}${TAG_OK}  已注册  ${RES} ${DIM} 终端输入 TGR 即可唤出${RES}"
    else
        echo -e "${MAIN} │  ${DIM}全局环境    ${RES}${TAG_ERR}  未注册  ${RES}"
    fi
    
    echo -e "${MAIN} │${RES}"

    # --- 操作树 ---
    echo -e "${MAIN} ├─ ${B}${TEXT}执行指令${RES}"
    echo -e "${MAIN} │  ${B}1${RES}  一键全自动部署"
    echo -e "${MAIN} │  ${B}2${RES}  平滑停止服务"
    echo -e "${MAIN} │  ${B}3${RES}  启动守护进程"
    echo -e "${MAIN} │  ${B}4${RES}  重启雷达引擎"
    echo -e "${MAIN} │${RES}"
    
    # --- 维护树 ---
    echo -e "${MAIN} ├─ ${B}${TEXT}进阶维护${RES}"
    echo -e "${MAIN} │  ${B}5${RES}  查看状态与实时日志"
    echo -e "${MAIN} │  ${B}6${RES}  刷新监听账号授权"
    echo -e "${MAIN} │  ${B}7${RES}  彻底卸载引擎组件"
    echo -e "${MAIN} │${RES}"
    echo -e "${MAIN} │  ${DIM}0  退出控制台${RES}"
    echo -e "${MAIN} │${RES}"
}

show_loading() {
    local msg=$1
    echo -ne "\n${MAIN} ⠋ ${RES} ${msg}"
    for i in {1..3}; do echo -ne "."; sleep 0.2; done
    echo -e " ${TAG_OK} 完成 ${RES}"
}

while true; do
    show_menu
    printf "${MAIN} ╰─➤ ${RES}${B}请选择指令 [0-7]: ${RES}"
    read -r opt

    case $opt in
        1)
            echo -e "\n${MAIN} ⠋ ${RES} 正在拉取云端部署向导..."
            sleep 0.5
            bash <(curl -fsSL https://raw.githubusercontent.com/chenmo8848/TG-Radar/main/install.sh)
            break
            ;;
        2)
            show_loading "正在优雅停止服务"
            sudo systemctl stop $SERVICE_NAME
            sleep 0.8
            ;;
        3)
            show_loading "正在唤醒雷达引擎"
            sudo systemctl start $SERVICE_NAME
            sleep 0.8
            ;;
        4)
            show_loading "正在重载所有组件"
            sudo systemctl restart $SERVICE_NAME
            sleep 0.8
            ;;
        5)
            clear
            echo -e "\n${MAIN}${B} ▌ 实时日志流 (按 q 退出追踪) ${RES}\n"
            sudo systemctl status $SERVICE_NAME --no-pager || true
            echo -e "\n${DIM} -------------------------------------------------- ${RES}\n"
            journalctl -u $SERVICE_NAME -n 20 --no-pager
            echo -e "\n${MAIN} ╰─➤ ${RES}按【回车键】返回主控制台..."
            read
            ;;
        6)
            echo -e "\n${MAIN} ⠋ ${RES} 正在清除过期 Session..."
            cd "$INSTALL_DIR" && rm -f *.session*
            sleep 1
            echo -e "  ${TAG_OK} 清理完毕 ${RES} 请重新执行 [1] 扫码授权。"
            sleep 2
            ;;
        7)
            echo -e "\n${TAG_ERR} 警告 ${RES} ${B}这将彻底抹除 TG-Radar 的所有数据与配置。${RES}"
            read -p "      请确认是否继续？(y/n): " confirm
            if [[ $confirm == [yY] ]]; then
                show_loading "正在执行粉碎协议"
                sudo systemctl stop $SERVICE_NAME || true
                sudo systemctl disable $SERVICE_NAME || true
                sudo rm -f /etc/systemd/system/$SERVICE_NAME.service
                sudo systemctl daemon-reload
                sudo rm -f /usr/local/bin/TGR
                echo -e "      ${TAG_OK} 卸载成功 ${RES} 期待再次相见。"
                exit 0
            fi
            ;;
        0)
            echo -e "\n      ${TAG_OK} 已退出 ${RES} 随时输入 TGR 唤出面板。\n"
            exit 0
            ;;
        *)
            echo -e "\n      ${TAG_ERR} 错误 ${RES} 无效指令，请重新输入。"
            sleep 1
            ;;
    esac
done