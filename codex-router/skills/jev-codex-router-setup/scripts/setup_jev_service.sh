#!/usr/bin/env bash
#
# 按平台安装 Jev 常驻服务。
#
#   bash setup_jev_service.sh                 # macOS launchd / Linux systemd
#   bash setup_jev_service.sh --print-only    # 只打印 unit/plist，不落盘
#   bash setup_jev_service.sh --repo ~/src/jev-codex-router
#
# macOS 复用仓库自带的 server/install-service.sh（含 CA bundle 处理）；Linux 生成
# systemd user unit；Windows/WSL 给出等价指令，不假装能装 launchd/systemd。
set -euo pipefail

REPO="${JEV_REPO:-}"
PRINT_ONLY=0
LABEL="${JEV_ROUTER_LABEL:-com.thibaultsaintjean.jev-router}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --print-only) PRINT_ONLY=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

find_repo() {
  [[ -n "$REPO" ]] && { printf '%s' "$REPO"; return 0; }
  local candidate
  for candidate in "$PWD" "$HOME/AIProjects/agent-labs/jev-codex-router" "$HOME/code/jev-codex-router" "$HOME/src/jev-codex-router"; do
    [[ -f "$candidate/server/jev_server.py" ]] && { printf '%s' "$candidate"; return 0; }
  done
  return 1
}

REPO="$(find_repo || true)"
if [[ -z "$REPO" || ! -f "$REPO/server/jev_server.py" ]]; then
  echo "找不到 Jev 仓库（server/jev_server.py）。用 --repo 指定，或设 JEV_REPO。" >&2
  exit 1
fi
REPO="$(cd "$REPO" && pwd)"

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "找不到 python3（Jev server 需要 >= 3.11）。" >&2
  exit 1
fi
PY_VERSION="$("$PYTHON" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"

CA_FILE="${SSL_CERT_FILE:-}"
if [[ ! -r "${CA_FILE:-/nonexistent}" ]]; then
  CA_FILE=""
  for candidate in /etc/ssl/cert.pem /etc/ssl/certs/ca-certificates.crt \
                   "${HOMEBREW_PREFIX:-/opt/homebrew}/etc/ca-certificates/cacert.pem" \
                   /usr/local/etc/ca-certificates/cacert.pem; do
    if [[ -r "$candidate" ]]; then CA_FILE="$candidate"; break; fi
  done
fi

health_check() {
  local i
  for i in $(seq 1 20); do
    if curl -s -m 3 http://127.0.0.1:4319/health | grep -q '"ok"'; then
      curl -s -m 3 http://127.0.0.1:4319/health
      echo
      return 0
    fi
    sleep 1
  done
  return 1
}

platform="$(uname -s)"

case "$platform" in
  Darwin)
    if [[ -f "$REPO/server/install-service.sh" ]]; then
      if [[ "$PRINT_ONLY" == "1" ]]; then
        echo "# macOS: 仓库自带安装脚本，会写入 ~/Library/LaunchAgents/$LABEL.plist"
        echo "bash $REPO/server/install-service.sh"
        exit 0
      fi
      echo "==> 运行仓库自带的 launchd 安装脚本"
      echo "    (若 launchctl 在受管环境里被拒绝，请在你自己的终端重跑同一命令)"
      if bash "$REPO/server/install-service.sh"; then
        echo "launchd 安装完成。"
      else
        echo "launchd 安装失败；请在自己的终端执行：" >&2
        echo "  bash $REPO/server/install-service.sh" >&2
        echo "  或改用 watchdog：crontab -e 加入 '*/5 * * * * $REPO/server/watchdog.sh'" >&2
        exit 1
      fi
    fi
    ;;
  Linux)
    unit_dir="$HOME/.config/systemd/user"
    unit_file="$unit_dir/jev-router.service"
    unit="$(cat <<EOF
[Unit]
Description=Jev Codex Router
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO
ExecStart=$PYTHON $REPO/server/jev_server.py
Restart=always
RestartSec=3
$( [[ -n "$CA_FILE" ]] && echo "Environment=SSL_CERT_FILE=$CA_FILE" )

[Install]
WantedBy=default.target
EOF
)"
    if [[ "$PRINT_ONLY" == "1" ]]; then
      echo "# 写入 $unit_file"
      printf '%s\n' "$unit"
      exit 0
    fi
    if ! command -v systemctl >/dev/null 2>&1 || ! systemctl --user show-environment >/dev/null 2>&1; then
      echo "systemd --user 不可用（WSL/容器常见）。改用 watchdog + cron：" >&2
      echo "  */5 * * * * $REPO/server/watchdog.sh" >&2
      exit 1
    fi
    mkdir -p "$unit_dir"
    printf '%s\n' "$unit" > "$unit_file"
    systemctl --user daemon-reload
    systemctl --user enable --now jev-router
    echo "systemd user unit 已启用；日志：journalctl --user -u jev-router -f"
    ;;
  *)
    cat <<EOF
未识别的平台：$platform

Windows（原生）没有 launchd/systemd。两种等价做法：

1) 计划任务（PowerShell，改成你的路径后执行一次）：
   \$action  = New-ScheduledTaskAction -Execute "$PYTHON" -Argument "$REPO\\server\\jev_server.py"
   \$trigger = New-ScheduledTaskTrigger -AtLogOn
   Register-ScheduledTask -TaskName "JevCodexRouter" -Action \$action -Trigger \$trigger -RunLevel Limited

2) 登录自启目录放一个快捷方式，目标为：
   $PYTHON $REPO\\server\\jev_server.py

WSL 里可以直接用上面的 Linux/systemd 分支；若 WSL 没开 systemd，用 watchdog + cron。
EOF
    exit 0
    ;;
esac

echo
echo "Python：$PY_VERSION；CA bundle：${CA_FILE:-未找到（https 调用可能失败）}"
if health_check; then
  echo "Jev 服务健康检查通过。"
else
  echo "服务已安装但健康检查未通过；看日志后再重试。" >&2
  exit 1
fi
