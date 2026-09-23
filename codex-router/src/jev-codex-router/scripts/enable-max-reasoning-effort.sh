#!/usr/bin/env bash
#
# 兼容入口：让 Codex 客户端把 max（最高）档放进模型控件。
#
# 实现在 jev-codex-router-setup skill 里（跨平台、幂等、带备份）：
#   ~/.codex/skills/jev-codex-router-setup/scripts/enable_max_effort.sh
#
# 保留本文件是为了让仓库里的旧路径继续可用。
set -euo pipefail

CANONICAL="${CODEX_HOME:-$HOME/.codex}/skills/jev-codex-router-setup/scripts/enable_max_effort.sh"

if [[ ! -x "$CANONICAL" ]]; then
  cat >&2 <<EOF
找不到 skill 里的实现：$CANONICAL

请先安装 skill（把 jev-codex-router-setup 放进 \$CODEX_HOME/skills），
或直接手动：退出 Codex，在 \$CODEX_HOME/.codex-global-state.json 的
electron-persisted-atom-state.enabled-reasoning-efforts 里加上 "max"，再打开 Codex。
EOF
  exit 1
fi

exec "$CANONICAL" "$@"
