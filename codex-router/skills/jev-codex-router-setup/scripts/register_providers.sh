#!/usr/bin/env bash
#
# 注册（或修正）Codex Router 的 generic provider 描述。幂等，可重复执行。
#
#   bash register_providers.sh --providers aihubmix,ccsub,jev
#   bash register_providers.sh --dry-run
#
# 只写 provider 描述（名称/base URL/adapter/allow-private），不碰任何密钥；
# 密钥请让用户在系统终端里跑 configure_secrets.sh（隐藏输入）。
set -euo pipefail

# id|display name|base url|description|allow-private(0/1)
ALL_PROVIDERS=(
  "jev|Jev Router|http://127.0.0.1:4319/v1|Jev 自动路由（本机）|1"
  "aihubmix|AIHubMix|https://aihubmix.com/v1|AIHubMix 官方 OpenAI Responses 兼容端点|0"
  "ccsub|CCSub|https://ccsub.inferera.com/v1|CCSub sub2api provider|0"
)

PROVIDERS="aihubmix,ccsub,jev"
DRY_RUN=0
BIN="${CODEX_ROUTER_BIN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --providers) PROVIDERS="$2"; shift 2 ;;
    --bin) BIN="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

find_router() {
  if [[ -n "$BIN" && -e "$BIN" ]]; then printf '%s' "$BIN"; return 0; fi
  if command -v codex-router >/dev/null 2>&1; then command -v codex-router; return 0; fi
  for root in "${CODEX_ROUTER_SOURCE_ROOT:-}" "$HOME/.local/share/codex-router" "$HOME/codex-router"; do
    [[ -n "$root" ]] || continue
    for name in codex-router codex-router.cmd codex-router.ps1; do
      if [[ -e "$root/bin/$name" ]]; then printf '%s' "$root/bin/$name"; return 0; fi
    done
  done
  return 1
}

BIN="$(find_router || true)"
if [[ -z "$BIN" ]]; then
  echo "未找到 codex-router。先安装 Codex Router，或用 --bin 指定路径。" >&2
  exit 1
fi

run_router() {
  case "$BIN" in
    *.ps1) powershell.exe -NoProfile -File "$BIN" "$@" ;;
    *) "$BIN" "$@" ;;
  esac
}

IFS=',' read -r -a WANTED <<< "$PROVIDERS"

spec_for() {
  local id="$1"
  local entry
  for entry in "${ALL_PROVIDERS[@]}"; do
    if [[ "${entry%%|*}" == "$id" ]]; then printf '%s' "$entry"; return 0; fi
  done
  return 1
}

for id in "${WANTED[@]}"; do
  id="$(printf '%s' "$id" | tr -d '[:space:]')"
  [[ -n "$id" ]] || continue
  if ! spec="$(spec_for "$id")"; then
    echo "校准表里没有 provider：$id" >&2
    exit 2
  fi
  IFS='|' read -r pid name url desc allow_private <<< "$spec"

  args=(--name "$name" --base-url "$url" --adapter openai-responses --description "$desc")
  [[ "$allow_private" == "1" ]] && args+=(--allow-private)

  if run_router providers generic show "$pid" >/dev/null 2>&1; then
    action="edit"
  else
    action="add"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[dry-run] providers generic $action $pid ${args[*]}"
    echo "[dry-run] providers generic enable $pid"
    continue
  fi

  echo "==> providers generic $action $pid"
  run_router providers generic "$action" "$pid" "${args[@]}" >/dev/null
  run_router providers generic enable "$pid" >/dev/null
  echo "    已注册并启用：$name"
done

if [[ "$DRY_RUN" == "0" ]]; then
  echo
  run_router providers generic list
fi
