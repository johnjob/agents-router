#!/usr/bin/env bash
# 为独立的 Codex Router checkout 应用原生子任务转发补丁。
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH_DIR="$SKILL_DIR/assets/patches"
ROUTER_ROOT="${CODEX_ROUTER_SOURCE_ROOT:-}"
CHECK_ONLY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --router) ROUTER_ROOT="${2:-}"; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help)
      echo "用法：bash scripts/apply_native_agent_relay_patches.sh --router <Codex Router checkout> [--check]"
      exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [ -z "$ROUTER_ROOT" ] && [ -n "${CODEX_ROUTER_BIN:-}" ]; then
  ROUTER_ROOT="$(cd "$(dirname "$CODEX_ROUTER_BIN")/.." && pwd -P)"
fi
if [ -z "$ROUTER_ROOT" ]; then
  ROUTER_ROOT="$HOME/.local/share/codex-router"
fi
if [ ! -f "$ROUTER_ROOT/src/router.mjs" ] ||
   [ "$(git -C "$ROUTER_ROOT" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
  echo "未找到 Codex Router 源码 checkout：$ROUTER_ROOT" >&2
  exit 1
fi

latest="$PATCH_DIR/0003-native-agent-model-rejection.patch"
if git -C "$ROUTER_ROOT" apply --reverse --check "$latest" 2>/dev/null; then
  echo "0002、0003：已应用"
  exit 0
fi

# 先在临时副本顺序演练，避免第二份补丁失败时留下只应用 0002 的 checkout。
python3 - "$ROUTER_ROOT" "$PATCH_DIR" <<'PY'
import pathlib
import shutil
import subprocess
import sys
import tempfile

root = pathlib.Path(sys.argv[1])
patches = [pathlib.Path(sys.argv[2]) / name for name in (
    "0002-native-agent-relay-model.patch",
    "0003-native-agent-model-rejection.patch",
)]

with tempfile.TemporaryDirectory(prefix="codex-router-relay-") as directory:
    scratch = pathlib.Path(directory)
    for name in ("src/router.mjs", "test/routing.test.mjs"):
        target = scratch / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)

    def applies(patch, reverse=False):
        args = ["git", "apply"]
        if reverse:
            args.append("--reverse")
        args.extend(("--check", str(patch)))
        return subprocess.run(
            args, cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0

    for patch in patches:
        if applies(patch, reverse=True):
            continue
        if not applies(patch):
            raise SystemExit(f"补丁序列与源码不兼容（检查到 {patch.name}）；目标 checkout 未作改动。")
        subprocess.run(
            ["git", "apply", str(patch)], cwd=scratch, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
PY

for name in 0002-native-agent-relay-model.patch 0003-native-agent-model-rejection.patch; do
  patch="$PATCH_DIR/$name"
  if git -C "$ROUTER_ROOT" apply --reverse --check "$patch" 2>/dev/null; then
    echo "${name}：已应用"
  elif git -C "$ROUTER_ROOT" apply --check "$patch" 2>/dev/null; then
    if [ "$CHECK_ONLY" -eq 1 ]; then
      echo "${name}：尚未应用"
      exit 1
    fi
    git -C "$ROUTER_ROOT" apply "$patch"
    echo "${name}：已应用"
  else
    echo "$name 与 $ROUTER_ROOT 中的源码不兼容；未覆盖现有改动。" >&2
    exit 2
  fi
done
