#!/bin/bash
# =============================================================================
# kiro-devops 一键部署助手
# 支持: 飞书(Lark) / 微信(iLink) / 双平台
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# -----------------------------------------------------------------------------
# 确保用 bash 执行
# -----------------------------------------------------------------------------
if [ -z "$BASH_VERSION" ]; then
    echo "请用 bash 执行此脚本: bash $0"
    exit 1
fi

# -----------------------------------------------------------------------------
# 颜色定义 (POSIX printf，兼容所有 shell)
# -----------------------------------------------------------------------------
RED=$(printf '\033[0;31m')
GREEN=$(printf '\033[0;32m')
YELLOW=$(printf '\033[1;33m')
BLUE=$(printf '\033[0;34m')
CYAN=$(printf '\033[0;36m')
BOLD=$(printf '\033[1m')
NC=$(printf '\033[0m') # No Color

# -----------------------------------------------------------------------------
# 工具函数
# -----------------------------------------------------------------------------
info()    { printf "%b\n" "${BLUE}ℹ ${NC}$1"; }
success() { printf "%b\n" "${GREEN}✅ ${NC}$1"; }
warn()    { printf "%b\n" "${YELLOW}⚠️  ${NC}$1"; }
error()   { printf "%b\n" "${RED}❌ ${NC}$1"; }
header()  { printf "%b\n" "\n${BOLD}${CYAN}$1${NC}"; printf "%b\n" "${CYAN}$(printf '=%.0s' $(seq 1 ${#1}))${NC}\n"; }

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 更新或追加 .env 变量
update_env_var() {
    local key="$1"
    local value="$2"
    local env_file="$SCRIPT_DIR/.env"

    # 创建 .env 如果不存在
    if [ ! -f "$env_file" ]; then
        touch "$env_file"
    fi

    # 如果变量已存在则替换，否则追加
    if grep -qE "^export ${key}=" "$env_file" 2>/dev/null; then
        sed -i "s|^export ${key}=.*|export ${key}=${value}|" "$env_file"
    elif grep -qE "^${key}=" "$env_file" 2>/dev/null; then
        sed -i "s|^${key}=.*|export ${key}=${value}|" "$env_file"
    else
        echo "export ${key}=${value}" >> "$env_file"
    fi
}

# 读取 .env 变量（如果存在）
get_env_var() {
    local key="$1"
    local default="$2"
    local env_file="$SCRIPT_DIR/.env"

    if [ -f "$env_file" ]; then
        local val
        val=$(grep -E "^export ${key}=" "$env_file" 2>/dev/null | cut -d'=' -f2-)
        if [ -z "$val" ]; then
            val=$(grep -E "^${key}=" "$env_file" 2>/dev/null | cut -d'=' -f2-)
        fi
        echo "${val:-$default}"
    else
        echo "$default"
    fi
}

# -----------------------------------------------------------------------------
# 依赖检查
# -----------------------------------------------------------------------------
check_deps() {
    header "🔍 环境检查"

    local missing=()

    if ! command_exists python3; then
        missing+=("python3")
    fi

    if ! python3 -c "import qrcode" 2>/dev/null; then
        warn "qrcode 库未安装，正在安装..."
        pip3 install qrcode -q 2>/dev/null || pip install qrcode -q 2>/dev/null || {
            error "无法安装 qrcode，请手动运行: pip3 install qrcode"
            missing+=("python3-qrcode")
        }
    fi

    if ! command_exists lark_oapi 2>/dev/null && ! python3 -c "import lark_oapi" 2>/dev/null; then
        warn "lark-oapi 未安装，正在安装..."
        pip3 install lark-oapi -q 2>/dev/null || pip install lark-oapi -q 2>/dev/null || {
            error "无法安装 lark-oapi"
            missing+=("lark-oapi")
        }
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        error "缺少依赖: ${missing[*]}"
        echo "请安装后重试: sudo apt-get install python3-pip && pip3 install qrcode lark-oapi"
        exit 1
    fi

    success "环境检查通过"
}

# -----------------------------------------------------------------------------
# AWS 配置检查
# -----------------------------------------------------------------------------
check_aws() {
    # 检查 boto3 是否安装（可选依赖）
    if ! python3 -c "import boto3" 2>/dev/null; then
        return 0
    fi

    # 检查是否已配置 AWS 凭证
    local has_creds=false
    if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
        has_creds=true
    elif [ -f "${HOME}/.aws/credentials" ] || [ -f "${HOME}/.aws/config" ]; then
        has_creds=true
    elif curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/iam/security-credentials/ >/dev/null 2>&1; then
        has_creds=true
    fi

    if [ "$has_creds" = false ]; then
        warn "检测到已安装 boto3，但未找到 AWS 凭证配置"
        echo ""
        echo "如需使用 Dashboard Resources（AWS EC2/RDS 自动发现 + CloudWatch 指标），"
        echo "请先配置 AWS Profile 或凭证，以下方式任选其一："
        echo "  1. aws configure 命令配置 ~/.aws/credentials"
        echo "  2. 在 .env 中设置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY"
        echo "  3. 为 EC2 实例挂载 IAM Role"
        echo ""
        echo "建议的 IAM 最小权限（ReadOnly）："
        echo "  • ec2:DescribeInstances"
        echo "  • rds:DescribeDBInstances"
        echo "  • cloudwatch:GetMetricStatistics"
        echo "  • cloudwatch:ListMetrics"
        echo ""
        read -p "按 Enter 继续..."
    else
        info "AWS 凭证已配置"
    fi
}

# -----------------------------------------------------------------------------
# 飞书配置
# -----------------------------------------------------------------------------
setup_feishu() {
    header "📋 飞书 (Lark) 配置"

    local current_app_id
    current_app_id=$(get_env_var "FEISHU_APP_ID" "")

    if [ -n "$current_app_id" ]; then
        info "当前已配置 App ID: ${current_app_id}"
        read -p "是否重新配置？(y/N): " reconfig
        if [[ "$reconfig" != "y" && "$reconfig" != "Y" ]]; then
            info "保留现有飞书配置"
            return 0
        fi
    fi

    echo ""
    echo "请前往 https://open.feishu.cn/app 获取以下信息："
    echo "  1. 创建企业自建应用"
    echo "  2. 记录 App ID 和 App Secret"
    echo "  3. 添加「机器人」能力"
    echo "  4. 权限管理 → 开通 im:message、im:message:send_as_bot"
    echo ""

    read -p "飞书 App ID (如 cli_xxx): " app_id
    while [ -z "$app_id" ]; do
        error "App ID 不能为空"
        read -p "飞书 App ID: " app_id
    done

    read -s -p "飞书 App Secret: " app_secret
    echo ""
    while [ -z "$app_secret" ]; do
        error "App Secret 不能为空"
        read -s -p "飞书 App Secret: " app_secret
        echo ""
    done

    update_env_var "FEISHU_APP_ID" "$app_id"
    update_env_var "FEISHU_APP_SECRET" "$app_secret"

    success "飞书配置已保存到 .env"
}

# -----------------------------------------------------------------------------
# 微信配置
# -----------------------------------------------------------------------------
setup_weixin() {
    header "📱 微信 (iLink) 配置"

    if [ -f "$HOME/.kiro/weixin_token.json" ]; then
        info "检测到已保存的微信 token: $HOME/.kiro/weixin_token.json"
        read -p "是否重新扫码登录？(y/N): " relogin
        if [[ "$relogin" != "y" && "$relogin" != "Y" ]]; then
            info "保留现有微信配置"
            update_env_var "WEIXIN_BOT_TOKEN" ""
            return 0
        fi
        rm -f "$HOME/.kiro/weixin_token.json"
    fi

    echo ""
    echo "即将启动微信扫码登录流程..."
    echo "请确保手机微信可以扫描二维码"
    echo ""
    read -p "按 Enter 开始扫码..."

    if ! python3 "$SCRIPT_DIR/scripts/setup_weixin.py"; then
        error "微信扫码登录失败"
        return 1
    fi

    # token 已由 setup_weixin.py 保存到 ~/.kiro/weixin_token.json
    # gateway.py 启动时会自动读取
    update_env_var "WEIXIN_BOT_TOKEN" ""

    success "微信配置完成"
}

# -----------------------------------------------------------------------------
# Kiro CLI 配置
# -----------------------------------------------------------------------------
setup_kiro() {
    header "🤖 Kiro CLI 配置"

    local current_timeout
    current_timeout=$(get_env_var "KIRO_TIMEOUT" "120")

    read -p "Kiro CLI 同步超时（秒）[当前: ${current_timeout}, 默认 120]: " timeout
    timeout=${timeout:-$current_timeout}
    update_env_var "KIRO_TIMEOUT" "$timeout"

    local current_agent
    current_agent=$(get_env_var "KIRO_AGENT" "")
    read -p "指定 Kiro Agent（可选，留空使用默认）[当前: ${current_agent}]: " agent
    if [ -n "$agent" ]; then
        update_env_var "KIRO_AGENT" "$agent"
    fi

    success "Kiro CLI 配置完成"
}

# -----------------------------------------------------------------------------
# 记忆系统配置
# -----------------------------------------------------------------------------
setup_memory() {
    header "🧠 记忆系统配置"

    local current
    current=$(get_env_var "ENABLE_MEMORY" "false")

    echo "记忆功能基于 SQLite，零额外依赖"
    echo ""
    read -p "启用记忆功能？(y/N) [当前: ${current}]: " mem
    if [[ "$mem" == "y" || "$mem" == "Y" ]]; then
        update_env_var "ENABLE_MEMORY" "true"
        success "记忆功能已启用"
    else
        update_env_var "ENABLE_MEMORY" "false"
        info "记忆功能已关闭"
    fi
}

# -----------------------------------------------------------------------------
# Webhook 告警配置
# -----------------------------------------------------------------------------
setup_webhook() {
    header "🚨 Webhook 告警配置"

    local current
    current=$(get_env_var "WEBHOOK_ENABLED" "false")

    read -p "启用 Webhook 告警接收？(y/N) [当前: ${current}]: " webhook
    if [[ "$webhook" == "y" || "$webhook" == "Y" ]]; then
        update_env_var "WEBHOOK_ENABLED" "true"

        local current_port
        current_port=$(get_env_var "WEBHOOK_PORT" "8080")
        read -p "Webhook 端口 [当前: ${current_port}]: " port
        port=${port:-$current_port}
        update_env_var "WEBHOOK_PORT" "$port"

        local current_host
        current_host=$(get_env_var "WEBHOOK_HOST" "127.0.0.1")
        read -p "Webhook 监听地址 (127.0.0.1=仅本机, 0.0.0.0=全网卡) [当前: ${current_host}]: " host
        host=${host:-$current_host}
        update_env_var "WEBHOOK_HOST" "$host"

        local current_token
        current_token=$(get_env_var "WEBHOOK_TOKEN" "")
        if [ -z "$current_token" ]; then
            current_token=$(openssl rand -hex 16 2>/dev/null || cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 32)
        fi
        info "说明：WEBHOOK_TOKEN 用于外部系统（Prometheus Alertmanager / CloudWatch Lambda / Jenkins 等）调用 Webhook 时鉴权"
        info "      在 Prometheus alertmanager.yml 中配置：bearer_token: '<此处的 token>'"
        read -p "Webhook Token (外部系统调用鉴权) [当前: ${current_token}]: " token
        token=${token:-$current_token}
        update_env_var "WEBHOOK_TOKEN" "$token"

        success "Webhook 已启用，地址: http://${host}:${port}/event"
        info "Prometheus 示例："
        info "  webhook_configs:"
        info "    - url: 'http://${host}:${port}/event'"
        info "      http_config:"
        info "        bearer_token: '${token}'"
    else
        update_env_var "WEBHOOK_ENABLED" "false"
        info "Webhook 已关闭"
    fi
}

# -----------------------------------------------------------------------------
# 告警推送配置
# -----------------------------------------------------------------------------
setup_alert() {
    header "📢 告警推送配置"

    local current_targets
    current_targets=$(get_env_var "ALERT_NOTIFY_TARGETS" "")

    # 检测已配置的平台
    local has_feishu has_weixin
    has_feishu=$(get_env_var "FEISHU_APP_ID" "")
    if [ -f "$HOME/.kiro/weixin_token.json" ]; then
        has_weixin="yes"
    fi

    echo "告警触发后，分析结果会自动推送到以下目标"
    echo ""

    if [ -n "$current_targets" ]; then
        info "当前推送目标: ${current_targets}"
    fi

    # 构建选项菜单
    local options=""
    local opt_idx=1
    local opt_feishu="" opt_weixin="" opt_both=""

    if [ -n "$has_feishu" ]; then
        options="${options}\n  ${opt_idx}) 仅飞书推送"
        opt_feishu=$opt_idx
        opt_idx=$((opt_idx + 1))
    fi
    if [ -n "$has_weixin" ]; then
        options="${options}\n  ${opt_idx}) 仅微信推送"
        opt_weixin=$opt_idx
        opt_idx=$((opt_idx + 1))
    fi
    if [ -n "$has_feishu" ] && [ -n "$has_weixin" ]; then
        options="${options}\n  ${opt_idx}) 飞书 + 微信同时推送"
        opt_both=$opt_idx
        opt_idx=$((opt_idx + 1))
    fi
    options="${options}\n  ${opt_idx}) 不推送"
    local opt_none=$opt_idx

    if [ -z "$has_feishu" ] && [ -z "$has_weixin" ]; then
        warn "尚未配置飞书或微信，请先完成平台配置后再设置告警推送"
        return 0
    fi

    printf "%b\n" "${options}"
    read -p "请选择推送方式: " choice

    local targets=""

    if [ "$choice" = "$opt_feishu" ]; then
        # 仅飞书
        local old_user_id
        old_user_id=$(get_env_var "ALERT_NOTIFY_USER_ID" "")
        local default_id=""
        if [ -n "$old_user_id" ]; then
            default_id="$old_user_id"
        elif [ -n "$current_targets" ]; then
            # 尝试从当前 targets 提取飞书 ID
            default_id=$(echo "$current_targets" | grep -o 'feishu:[^,]*' | sed 's/feishu://')
        fi
        read -p "飞书用户 Open ID (ou_xxx) [当前: ${default_id:-无}]: " fid
        fid=${fid:-$default_id}
        if [ -n "$fid" ]; then
            targets="feishu:${fid}"
        fi

    elif [ "$choice" = "$opt_weixin" ]; then
        # 仅微信
        local default_wid=""
        if [ -n "$current_targets" ]; then
            default_wid=$(echo "$current_targets" | grep -o 'weixin:[^,]*' | sed 's/weixin://')
        fi
        read -p "微信用户 ID (wxid_xxx@im.wechat) [当前: ${default_wid:-无}]: " wid
        wid=${wid:-$default_wid}
        if [ -n "$wid" ]; then
            targets="weixin:${wid}"
        fi

    elif [ "$choice" = "$opt_both" ]; then
        # 飞书 + 微信
        local old_user_id
        old_user_id=$(get_env_var "ALERT_NOTIFY_USER_ID" "")
        local default_fid=""
        if [ -n "$old_user_id" ]; then
            default_fid="$old_user_id"
        elif [ -n "$current_targets" ]; then
            default_fid=$(echo "$current_targets" | grep -o 'feishu:[^,]*' | sed 's/feishu://')
        fi
        read -p "飞书用户 Open ID (ou_xxx) [当前: ${default_fid:-无}]: " fid
        fid=${fid:-$default_fid}

        local default_wid=""
        if [ -n "$current_targets" ]; then
            default_wid=$(echo "$current_targets" | grep -o 'weixin:[^,]*' | sed 's/weixin://')
        fi
        read -p "微信用户 ID (wxid_xxx@im.wechat) [当前: ${default_wid:-无}]: " wid
        wid=${wid:-$default_wid}

        if [ -n "$fid" ] && [ -n "$wid" ]; then
            targets="feishu:${fid},weixin:${wid}"
        elif [ -n "$fid" ]; then
            targets="feishu:${fid}"
        elif [ -n "$wid" ]; then
            targets="weixin:${wid}"
        fi

    elif [ "$choice" = "$opt_none" ]; then
        targets=""
        info "告警推送已关闭"
    else
        warn "无效选项，保持当前配置"
        return 0
    fi

    if [ -n "$targets" ]; then
        update_env_var "ALERT_NOTIFY_TARGETS" "$targets"
        success "告警推送目标已设置: ${targets}"
    else
        update_env_var "ALERT_NOTIFY_TARGETS" ""
    fi

    local current_severity
    current_severity=$(get_env_var "ALERT_AUTO_ANALYZE_SEVERITY" "high,critical")
    read -p "自动分析的严重级别 [当前: ${current_severity}]: " severity
    severity=${severity:-$current_severity}
    update_env_var "ALERT_AUTO_ANALYZE_SEVERITY" "$severity"
}

# -----------------------------------------------------------------------------
# Dashboard 配置
# -----------------------------------------------------------------------------
setup_dashboard() {
    header "🖥️  Dashboard 配置"

    local current_token
    current_token=$(get_env_var "DASHBOARD_TOKEN" "")

    if [ -z "$current_token" ]; then
        current_token=$(openssl rand -hex 16 2>/dev/null || cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 32)
    fi

    read -p "Dashboard 访问 Token（留空关闭面板）[当前: ${current_token:0:8}...]: " token
    if [ -n "$token" ]; then
        update_env_var "DASHBOARD_TOKEN" "$token"
        success "Dashboard 已启用，访问: http://<IP>:8080/dashboard/"
    else
        update_env_var "DASHBOARD_TOKEN" ""
        info "Dashboard 已关闭"
    fi
}

# -----------------------------------------------------------------------------
# CloudWatch 指标同步 Cron 配置
# -----------------------------------------------------------------------------
setup_metrics_sync_cron() {
    header "📊 CloudWatch 指标同步 Cron 配置"

    # 检查 boto3 是否安装（必须依赖）
    if ! python3 -c "import boto3" 2>/dev/null; then
        warn "boto3 未安装，跳过指标同步 Cron 配置"
        warn "如需使用，请先安装: pip3 install boto3"
        return 0
    fi

    # 检查 AWS 凭证
    local has_creds=false
    if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
        has_creds=true
    elif [ -f "${HOME}/.aws/credentials" ] || [ -f "${HOME}/.aws/config" ]; then
        has_creds=true
    elif curl -s --connect-timeout 1 http://169.254.169.254/latest/meta-data/iam/security-credentials/ >/dev/null 2>&1; then
        has_creds=true
    fi

    if [ "$has_creds" = false ]; then
        warn "未找到 AWS 凭证，跳过指标同步 Cron 配置"
        info "配置 AWS 凭证后，可手动运行: bash $SCRIPT_DIR/setup.sh 重新设置"
        return 0
    fi

    local current_cron
    current_cron=$(crontab -l 2>/dev/null | grep "sync_resource_metrics.py" || true)

    if [ -n "$current_cron" ]; then
        info "检测到已有指标同步 Cron 任务:"
        echo "  $current_cron"
        read -p "是否重新配置？(y/N): " reconfig
        if [[ "$reconfig" != "y" && "$reconfig" != "Y" ]]; then
            info "保留现有 Cron 配置"
            return 0
        fi
        # 删除旧条目
        crontab -l 2>/dev/null | grep -v "sync_resource_metrics.py" | crontab - 2>/dev/null || true
    fi

    echo ""
    echo "将配置每日自动同步 AWS EC2/RDS 的 CloudWatch CPU 指标"
    echo "  • 首次运行: 回溯填充过去 30 天数据"
    echo "  • 日常运行: 每日凌晨同步前 24 小时数据"
    echo "  • 数据存储: memory_db/raw_metrics_YYYY_MM.db + aggregated_metrics.db"
    echo ""
    read -p "启用 CloudWatch 指标同步定时任务？(y/N): " enable_cron

    if [[ "$enable_cron" == "y" || "$enable_cron" == "Y" ]]; then
        local cron_schedule="0 3 * * *"
        read -p "Cron 调度表达式 [默认: 0 3 * * * (每日凌晨3点)]: " schedule_input
        cron_schedule=${schedule_input:-$cron_schedule}

        local log_file="/var/log/kiro-metrics-sync.log"
        local cron_cmd="cd ${SCRIPT_DIR} && PYTHONPATH=${SCRIPT_DIR} /usr/bin/python3 ${SCRIPT_DIR}/scripts/sync_resource_metrics.py --incremental >> ${log_file} 2>&1"
        local cron_entry="${cron_schedule} ${cron_cmd}"

        # 添加到 crontab
        (crontab -l 2>/dev/null || true; echo "$cron_entry") | crontab -

        success "Cron 任务已添加"
        info "调度: ${cron_schedule}"
        info "日志: ${log_file}"
        info "手动执行: PYTHONPATH=${SCRIPT_DIR} python3 ${SCRIPT_DIR}/scripts/sync_resource_metrics.py --backfill"

        # 询问是否立即执行首次回溯填充
        echo ""
        read -p "是否立即执行首次回溯填充（同步过去30天数据）？(y/N): " backfill_now
        if [[ "$backfill_now" == "y" || "$backfill_now" == "Y" ]]; then
            info "开始回溯填充..."
            if PYTHONPATH="${SCRIPT_DIR}" python3 "${SCRIPT_DIR}/scripts/sync_resource_metrics.py" --backfill; then
                success "回溯填充完成"
            else
                error "回溯填充失败，请检查 AWS 权限和日志"
            fi
        fi
    else
        info "跳过 Cron 配置"
        info "之后可随时手动运行: PYTHONPATH=${SCRIPT_DIR} python3 ${SCRIPT_DIR}/scripts/sync_resource_metrics.py --incremental"
    fi
}

# -----------------------------------------------------------------------------
# systemd 服务安装
# -----------------------------------------------------------------------------
install_systemd() {
    header "⚙️  systemd 服务安装"

    if ! command_exists systemctl; then
        warn "当前系统不支持 systemd，跳过服务安装"
        return 0
    fi

    local user
    user=$(whoami)

    echo ""
    echo "将创建 systemd 服务: kiro-devops"
    echo "  用户: ${user}"
    echo "  工作目录: ${SCRIPT_DIR}"
    echo ""
    read -p "确认安装 systemd 服务？(y/N): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        info "跳过 systemd 安装"
        return 0
    fi

    # 检测 Python 路径
    local python_path
    if [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
        python_path="$SCRIPT_DIR/venv/bin/python3"
    else
        python_path="$(which python3)"
    fi

    # 生成 service 文件
    cat > /tmp/kiro-devops.service <<EOF
[Unit]
Description=kiro-devops gateway (Feishu + WeChat + Webhook)
After=network.target

[Service]
Type=simple
User=${user}
WorkingDirectory=${SCRIPT_DIR}
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStartPre=/bin/bash -c 'source ${SCRIPT_DIR}/.env'
ExecStart=${python_path} ${SCRIPT_DIR}/gateway.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    sudo cp /tmp/kiro-devops.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable kiro-devops

    success "systemd 服务已安装"
    echo ""
    printf "%b\n" "${CYAN}管理命令:${NC}"
    echo "  启动: ${BOLD}sudo systemctl start kiro-devops${NC}"
    echo "  停止: ${BOLD}sudo systemctl stop kiro-devops${NC}"
    echo "  状态: ${BOLD}sudo systemctl status kiro-devops${NC}"
    echo "  日志: ${BOLD}sudo journalctl -u kiro-devops -f${NC}"
    echo ""

    read -p "是否立即启动服务？(y/N): " start_now
    if [[ "$start_now" == "y" || "$start_now" == "Y" ]]; then
        sudo systemctl start kiro-devops
        sleep 2
        if sudo systemctl is-active --quiet kiro-devops; then
            success "服务已启动"
            sudo systemctl status kiro-devops --no-pager
        else
            error "服务启动失败，请检查日志"
            sudo journalctl -u kiro-devops --no-pager -n 20
        fi
    fi

    # 安装 CloudWatch 指标同步 cron
    setup_metrics_sync_cron
}

# -----------------------------------------------------------------------------
# 启动测试
# -----------------------------------------------------------------------------
start_test() {
    header "🚀 启动测试"

    echo ""
    echo "请选择启动方式："
    echo "  1) 前台运行（推荐测试，终端显示日志）"
    echo "  2) 后台运行（nohup，日志写入文件）"
    echo "  3) 跳过，稍后手动启动"
    echo ""
    read -p "请选择 [1-3]: " start_choice

    case $start_choice in
        1)
            info "前台启动 gateway..."
            echo "  按 Ctrl+C 停止"
            echo ""
            source "$SCRIPT_DIR/.env" && python3 "$SCRIPT_DIR/gateway.py"
            ;;
        2)
            info "后台启动 gateway..."
            source "$SCRIPT_DIR/.env" && nohup python3 "$SCRIPT_DIR/gateway.py" > /tmp/gateway.log 2>&1 &
            sleep 2
            success "gateway 已在后台运行"
            echo "  查看日志: tail -f /tmp/gateway.log"
            echo "  停止进程: pkill -f 'python3 gateway.py'"
            ;;
        *)
            info "跳过启动"
            ;;
    esac
}

# -----------------------------------------------------------------------------
# 主菜单
# -----------------------------------------------------------------------------
show_menu() {
    clear 2>/dev/null || true
    echo ""
    printf "%b\n" "${BOLD}${CYAN}╔════════════════════════════════════════════════════╗${NC}"
    printf "%b\n" "${BOLD}${CYAN}║${NC}     ${BOLD}kiro-devops 一键部署助手${NC}                    ${CYAN}║${NC}"
    printf "%b\n" "${BOLD}${CYAN}╠════════════════════════════════════════════════════╣${NC}"
    printf "%b\n" "${CYAN}║${NC}  支持平台: 飞书(Lark) | 微信(iLink)             ${CYAN}║${NC}"
    printf "%b\n" "${CYAN}║${NC}  功能: Kiro CLI 桥接 | 记忆 | 告警 | Dashboard   ${CYAN}║${NC}"
    printf "%b\n" "${BOLD}${CYAN}╚════════════════════════════════════════════════════╝${NC}"
    echo ""
    printf "%b\n" "${BOLD}请选择部署模式：${NC}"
    echo ""
    echo "  ${GREEN}[1]${NC} 仅飞书          — 企业 IM 接入"
    echo "  ${GREEN}[2]${NC} 仅微信          — 个人微信接入"
    echo "  ${GREEN}[3]${NC} 飞书 + 微信     — ${YELLOW}推荐，双通道同时运行${NC}"
    echo "  ${GREEN}[4]${NC} 仅配置通用项    — 告警/Webhook/Dashboard"
    echo "  ${GREEN}[5]${NC} 安装 systemd 服务 — 生产环境常驻"
    echo "  ${GREEN}[6]${NC} 退出"
    echo ""
}

# -----------------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------------
main() {
    # 检查依赖
    check_deps

    # 检查 AWS 配置（可选依赖，仅提示）
    check_aws

    # 显示菜单
    show_menu
    read -p "请输入选项 [1-6]: " choice
    echo ""

    case $choice in
        1)
            setup_feishu
            setup_kiro
            setup_memory
            setup_webhook
            setup_alert
            setup_dashboard
            install_systemd
            start_test
            ;;
        2)
            setup_weixin
            setup_kiro
            setup_memory
            setup_webhook
            setup_alert
            setup_dashboard
            install_systemd
            start_test
            ;;
        3)
            setup_feishu
            setup_weixin
            setup_kiro
            setup_memory
            setup_webhook
            setup_alert
            setup_dashboard
            install_systemd
            start_test
            ;;
        4)
            setup_kiro
            setup_memory
            setup_webhook
            setup_alert
            setup_dashboard
            setup_metrics_sync_cron
            ;;
        5)
            install_systemd
            ;;
        6)
            echo "再见！"
            exit 0
            ;;
        *)
            error "无效选项: $choice"
            exit 1
            ;;
    esac

    # 完成提示
    echo ""
    printf "%b\n" "${GREEN}╔════════════════════════════════════════════════════╗${NC}"
    printf "%b\n" "${GREEN}║${NC}           ${BOLD}🎉 配置完成！${NC}                          ${GREEN}║${NC}"
    printf "%b\n" "${GREEN}╚════════════════════════════════════════════════════╝${NC}"
    echo ""
    printf "%b\n" "配置已保存到: ${BOLD}.env${NC}"
    echo ""
    printf "%b\n" "${CYAN}常用命令:${NC}"
    echo "  前台运行:  ${BOLD}./start.sh${NC} 或 ${BOLD}source .env && python3 gateway.py${NC}"
    echo "  查看日志:  ${BOLD}tail -f /tmp/gateway.log${NC}"
    echo "  编辑配置:  ${BOLD}nano .env${NC}"
    echo ""
    printf "%b\n" "${CYAN}打包建议:${NC}"
    echo "  • systemd: sudo systemctl start kiro-devops"
    echo "  • Docker:  docker build -t kiro-devops . && docker run -d kiro-devops"
    echo "  • 裸机:    nohup ./start.sh &"
    echo ""
}

main "$@"

# ---------------------------------------------------------------------------
# Tencent Cloud dashboard enable/disable prompt
# ---------------------------------------------------------------------------
read -p "Enable Tencent Cloud dashboard? [y/N] " enable_tencent
enable_tencent=${enable_tencent:-N}
if [[ "$enable_tencent" =~ ^[Yy]$ ]]; then
    read -p "Tencent regions to monitor [ap-tokyo]: " tencent_regions
    tencent_regions=${tencent_regions:-ap-tokyo}
    # Convert space-separated to JSON array
    tencent_regions_json=$(echo "$tencent_regions" | tr ' ' '\n' | jq -R . | jq -s .)
    python3 -c "
import json, sys
with open('dashboard_config.json', 'r+') as f:
    cfg = json.load(f)
    cfg.setdefault('providers', {})
    cfg['providers']['tencent'] = {'enabled': True, 'regions': $tencent_regions_json}
    f.seek(0); json.dump(cfg, f, indent=2); f.truncate()
"
    if ! command -v tccli &> /dev/null; then
        echo "WARNING: tccli not found in PATH. Please install and configure it."
    fi
else
    python3 -c "
import json
with open('dashboard_config.json', 'r+') as f:
    cfg = json.load(f)
    cfg.setdefault('providers', {})
    cfg['providers']['tencent'] = {'enabled': False, 'regions': []}
    f.seek(0); json.dump(cfg, f, indent=2); f.truncate()
"
fi
