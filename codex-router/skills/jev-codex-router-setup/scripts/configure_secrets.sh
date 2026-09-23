#!/usr/bin/env bash
#
# 由**用户本人在系统终端**运行：录入 provider 密钥和 TypeSafe 密钥。
#
#   bash configure_secrets.sh --providers aihubmix,ccsub --typesafe
#
# 安全约束（不要放宽）：
#   * 必须在真实 tty 下运行；没有 tty 直接退出，避免密钥被 agent 的工具输出捕获。
#   * 不接受 --key 这类参数，密钥永不进入 argv、环境变量或日志。
#   * 只打印“已配置/跳过”，不打印密钥内容。
set -euo pipefail

PROVIDERS="aihubmix,ccsub"
DO_TYPESAFE=1
DO_CHATGPT_SESSION=1
BIN="${CODEX_ROUTER_BIN:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --providers) PROVIDERS="$2"; shift 2 ;;
    --typesafe) DO_TYPESAFE=1; shift ;;
    --no-typesafe) DO_TYPESAFE=0; shift ;;
    --chatgpt-session) DO_CHATGPT_SESSION=1; shift ;;
    --no-chatgpt-session) DO_CHATGPT_SESSION=0; shift ;;
    --bin) BIN="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [[ ! -t 0 || ! -r /dev/tty ]]; then
  cat >&2 <<'EOF'
这个脚本必须在交互式终端里运行。
请在你自己的 Terminal / iTerm 里执行，不要把密钥发给 agent，也不要让 agent 代跑。
EOF
  exit 1
fi

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
  echo "未找到 codex-router，先安装或用 --bin 指定。" >&2
  exit 1
fi

run_router() {
  case "$BIN" in
    *.ps1) powershell.exe -NoProfile -File "$BIN" "$@" ;;
    *) "$BIN" "$@" ;;
  esac
}

ask_yes_no() {           # ask_yes_no "问题" 默认(y/n)
  local question="$1" default="${2:-n}" answer
  local suffix="[y/N]"
  [[ "$default" == "y" ]] && suffix="[Y/n]"
  printf '%s %s ' "$question" "$suffix" > /dev/tty
  read -r answer < /dev/tty || answer=""
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy]$ ]]
}

set_env_var() {          # set_env_var 文件 KEY  （值从 stdin 读）
  local target="$1" key="$2"
  python3 - "$target" "$key" <<'PY'
import os, re, sys
from pathlib import Path

target = Path(sys.argv[1])
key = sys.argv[2]
value = sys.stdin.read().strip()
if not value:
    raise SystemExit("空值，未写入")

target.parent.mkdir(parents=True, exist_ok=True)
lines = []
if target.exists():
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()

pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=")
replaced = False
for index, line in enumerate(lines):
    if pattern.match(line):
        lines[index] = "{}={}".format(key, value)
        replaced = True
if not replaced:
    lines.append("{}={}".format(key, value))

target.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
os.chmod(target, 0o600)
print("已写入 {}".format(target))
PY
}

echo "== provider 密钥 =="
IFS=',' read -r -a WANTED <<< "$PROVIDERS"
for id in "${WANTED[@]}"; do
  id="$(printf '%s' "$id" | tr -d '[:space:]')"
  [[ -n "$id" ]] || continue
  if run_router providers generic credential "$id" status >/dev/null 2>&1; then
    if ! ask_yes_no "$id 已有密钥，是否覆盖？" n; then
      echo "  跳过 $id"
      continue
    fi
  elif ! ask_yes_no "现在录入 $id 的 API key？" y; then
    echo "  跳过 $id"
    continue
  fi
  echo "  请在隐藏提示里粘贴 $id 的 key（不会显示，也不会写进日志）："
  run_router providers generic credential "$id" set
done

if [[ "$DO_TYPESAFE" == "1" ]]; then
  echo
  echo "== TypeSafe / Jev =="
  # Jev server 按 ~/.hermes/.env -> ~/.jev.env -> 进程环境 的顺序读取；
  # 若前者已经定义了变量就更新它，否则写 ~/.jev.env。
  TARGET="$HOME/.jev.env"
  for candidate in "$HOME/.hermes/.env" "$HOME/.jev.env"; do
    if [[ -f "$candidate" ]] && grep -qE '^\s*(export\s+)?TYPESAFE_API_KEY\s*=' "$candidate"; then
      TARGET="$candidate"
      break
    fi
  done
  if ask_yes_no "现在录入 TYPESAFE_API_KEY（写入 $TARGET）？" y; then
    printf '请粘贴 TYPESAFE_API_KEY（隐藏输入）：' > /dev/tty
    read -rs TYPESAFE_VALUE < /dev/tty
    printf '\n' > /dev/tty
    if [[ -z "$TYPESAFE_VALUE" ]]; then
      echo "  空输入，跳过"
    else
      printf '%s\n' "$TYPESAFE_VALUE" | set_env_var "$TARGET" TYPESAFE_API_KEY
    fi
    unset TYPESAFE_VALUE
  fi
fi

if [[ "$DO_CHATGPT_SESSION" == "1" ]]; then
  echo
  echo "== 原生 ChatGPT 会话共享 =="
  if ask_yes_no "现在启用原生 GPT 会话共享（jev/auto 需要）？" y; then
    run_router chatgpt-session enable || echo "  未完成；稍后可重跑：$BIN chatgpt-session enable"
  fi
fi

echo
echo "== 状态汇总（只显示是否配置） =="
for id in "${WANTED[@]}"; do
  id="$(printf '%s' "$id" | tr -d '[:space:]')"
  [[ -n "$id" ]] || continue
  run_router providers generic credential "$id" status || true
done
for candidate in "$HOME/.hermes/.env" "$HOME/.jev.env"; do
  if [[ -f "$candidate" ]] && grep -qE '^\s*(export\s+)?TYPESAFE_API_KEY\s*=' "$candidate"; then
    echo "TYPESAFE_API_KEY 已配置（$candidate）"
    break
  fi
done
