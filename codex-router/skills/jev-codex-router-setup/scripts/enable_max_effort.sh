#!/usr/bin/env bash
#
# 让 Codex 客户端把 max（最高）档放进模型控件。
#
#   bash enable_max_effort.sh            # macOS 会自动退出/写入/重开 App
#   bash enable_max_effort.sh --dry-run
#   bash enable_max_effort.sh --no-restart
#
# 原因：客户端会把模型声明的档位与本地设置 enabled-reasoning-efforts 求交集，
# 该设置默认不含 max，所以声明 low/high/max 的模型只剩两档。
# 设置由运行中的桌面端保存在内存里并整体回写，运行期间改文件会被覆盖，因此
# macOS 分支必须「先退出 App，再写，再打开」。纯 CLI 用户不需要这一步。
set -euo pipefail

CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
STATE="$CODEX_HOME_DIR/.codex-global-state.json"
DRY_RUN=0
RESTART=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --state-file) STATE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-restart) RESTART=0; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

find_running_bundle() {
  ps -Ao command= 2>/dev/null \
    | grep -oE '/Applications/[^ ]+\.app/Contents/MacOS/[^ ]+' \
    | grep -E '/(ChatGPT|Codex)\.app/Contents/MacOS/ChatGPT$' \
    | head -n 1 || true
}

patch_state() {   # 读取 stdin 无；直接改 $STATE
  python3 - "$STATE" <<'PY'
import json, os, shutil, sys
from datetime import datetime
from pathlib import Path

state = Path(sys.argv[1])
if not state.exists():
    print("NO_STATE_FILE")
    raise SystemExit(0)

document = json.loads(state.read_text(encoding="utf-8"))
atoms = document.setdefault("electron-persisted-atom-state", {})
current = atoms.get("enabled-reasoning-efforts")

if current is None:
    target = ["low", "medium", "high", "xhigh", "ultra"]
else:
    target = list(current)
    for entry in ("low", "medium", "high", "xhigh", "ultra", "persistent"):
        if entry in current:
            continue
        # 只在原设置存在时补齐基础档，避免把旧客户端不认识的档位塞进去。
        if entry != "persistent":
            target.append(entry)

if "max" not in target:
    target.append("max")

if current == target:
    print("ALREADY:" + ",".join(target))
    raise SystemExit(0)

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup = state.with_name(state.name + ".bak-" + stamp + "-before-enable-max-effort")
shutil.copy2(state, backup)

atoms["enabled-reasoning-efforts"] = target
tmp = state.with_name(state.name + ".tmp")
tmp.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
os.replace(tmp, state)
print("UPDATED:" + ",".join(target))
print("BACKUP:" + str(backup))
PY
}

BUNDLE=""
if [[ "$(uname -s)" == "Darwin" ]]; then
  BUNDLE="$(find_running_bundle)"
fi

if [[ -n "$BUNDLE" && "$DRY_RUN" == "0" && "$RESTART" == "1" ]]; then
  APP_PATH="${BUNDLE%%/Contents/MacOS*}"
  echo "退出 $APP_PATH（写入后会自动重开）..."
  osascript -e "tell application \"$APP_PATH\" to quit" >/dev/null 2>&1 || true
  for _ in $(seq 1 120); do
    [[ -z "$(find_running_bundle)" ]] && break
    sleep 0.5
  done
  if [[ -n "$(find_running_bundle)" ]]; then
    echo "App 未在 60 秒内退出；请手动退出后重跑（或加 --no-restart 自行处理）。" >&2
    exit 1
  fi
  sleep 1
elif [[ -n "$BUNDLE" && "$RESTART" == "0" && "$DRY_RUN" == "0" ]]; then
  echo "注意：桌面端仍在运行且指定了 --no-restart；请自行退出后再重跑，否则设置会被内存状态覆盖。"
elif [[ -n "$BUNDLE" ]]; then
  echo "[dry-run] 会先退出 $BUNDLE"
fi

if [[ "$DRY_RUN" == "1" ]]; then
  python3 - "$STATE" <<'PY'
import json, sys
from pathlib import Path
state = Path(sys.argv[1])
if not state.exists():
    print("状态文件不存在：{}（纯 CLI 环境不需要这一步）".format(state))
    raise SystemExit(0)
atoms = json.loads(state.read_text(encoding="utf-8")).get("electron-persisted-atom-state", {})
print("当前 enabled-reasoning-efforts =", atoms.get("enabled-reasoning-efforts"))
PY
  exit 0
fi

RESULT="$(patch_state)"
case "$RESULT" in
  NO_STATE_FILE)
    echo "找不到 $STATE —— 这台机器只有 CLI、或桌面端从未启动过；纯 CLI 不需要启用 max。"
    exit 0
    ;;
  ALREADY:*)
    echo "max 档已在启用列表里：${RESULT#ALREADY:}"
    RESTART=0
    ;;
  *)
    echo "$RESULT" | sed 's/^UPDATED:/已写入 enabled-reasoning-efforts = /; s/^BACKUP:/备份：/'
    ;;
esac

if [[ "$RESTART" == "1" && -n "$BUNDLE" ]]; then
  APP_PATH="${BUNDLE%%/Contents/MacOS*}"
  open "$APP_PATH"
  echo "已重新打开 $APP_PATH"
elif [[ "$RESTART" == "1" && "$(uname -s)" != "Darwin" ]]; then
  echo "非 macOS：请确认桌面端已退出后再启动，否则设置会被内存状态覆盖。"
fi
