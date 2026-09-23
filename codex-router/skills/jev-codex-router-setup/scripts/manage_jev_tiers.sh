#!/usr/bin/env bash
#
# 增删改 Jev 正常路由的候选模型（转发到仓库里的 manage_route_models.py）。
#
#   bash manage_jev_tiers.sh list
#   bash manage_jev_tiers.sh add opencode-go-glm-5-3 --label glm --glyph "✨" \
#        --profile "GLM-5.3 via opencode Go." --effort-map medium=high,xhigh=max
#   bash manage_jev_tiers.sh replace aihubmix-deepseek-v4-1-flash --label deepseek --glyph "🐳"
#   bash manage_jev_tiers.sh remove gpt-6-astra
#   bash manage_jev_tiers.sh set-fallback --model gpt-6-astra --effort medium
#
# 模型 id 用 Codex Router 的 gatewayModel（如 aihubmix-deepseek-v4-1-flash），
# 不是 UI 的 aihubmix/... slug。要按 provider 批量增删模型请用
# calibrate_models.py --add/--remove。
#
# 默认在写入后跑服务端测试并重启 Jev 服务；用 --no-test / --no-restart 关闭。
set -euo pipefail

REPO="${JEV_REPO:-}"
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

find_repo() {
  [[ -n "$REPO" ]] && { printf '%s' "$REPO"; return 0; }
  local candidate
  for candidate in "$PWD" \
                   "$HOME/AIProjects/agent-labs/jev-codex-router" \
                   "$HOME/code/jev-codex-router" \
                   "$HOME/src/jev-codex-router"; do
    [[ -f "$candidate/server/jev_server.py" ]] && { printf '%s' "$candidate"; return 0; }
  done
  return 1
}

REPO="$(find_repo || true)"
MANAGER="$REPO/server/manage_route_models.py"
if [[ -z "$REPO" || ! -f "$MANAGER" ]]; then
  cat >&2 <<EOF
找不到 Jev 仓库里的 server/manage_route_models.py。
用 --repo 指定仓库路径，或设 JEV_REPO。
（该管理脚本在仓库内；旧 checkout 需要先更新仓库。）
EOF
  exit 1
fi

if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=(list)
fi

exec python3 "$MANAGER" "${ARGS[@]}"
