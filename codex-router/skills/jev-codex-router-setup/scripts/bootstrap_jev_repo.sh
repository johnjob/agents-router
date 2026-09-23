#!/usr/bin/env bash
# 把 Jev 仓库准备成这套 skill 需要的样子。
#
# 上游 0xNatoshi/jev-codex-router 里没有本 skill 依赖的三样东西：
#   server/route_models.json        候选池数据文件
#   server/manage_route_models.py   候选池增删改工具
#   routing_policy.py 的 registry 支持（含 FALLBACK/FRONTIER/ROUTE_LABELS/EFFORT_MAPS）
# 它们连同服务加固（launchd 的 SSL_CERT_FILE、JEV_API_URL 覆盖）一起放在
# assets/patches/0001-jev-registry-and-hardening.patch，本脚本负责 clone + 打补丁 + 校验。
#
#   bash scripts/bootstrap_jev_repo.sh --clone ~/AIProjects/agent-labs/jev-codex-router
#   bash scripts/bootstrap_jev_repo.sh --repo /path/to/existing/checkout
#   bash scripts/bootstrap_jev_repo.sh --check          # 只报告，不写任何文件
#
# 幂等：已经打好补丁就只做校验。checkout 里有未提交改动时默认拒绝打补丁（--force 可覆盖）。
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_PATCH="$SKILL_DIR/assets/patches/0001-jev-registry-and-hardening.patch"
UPSTREAM_URL="https://github.com/0xNatoshi/jev-codex-router.git"
PINNED_REF="faf46df90f3c6a3962bb5876d393c7d6552b7c8f"

REPO_ARG=""
CLONE_ARG=""
PATCH_ARG="$DEFAULT_PATCH"
REF_ARG="$PINNED_REF"
CHECK_ONLY=0
FORCE=0
RUN_TESTS=1

usage() {
  sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_ARG="${2:-}"; shift 2 ;;
    --clone) CLONE_ARG="${2:-}"; shift 2 ;;
    --patch) PATCH_ARG="${2:-}"; shift 2 ;;
    --ref) REF_ARG="${2:-}"; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    --force) FORCE=1; shift ;;
    --no-test) RUN_TESTS=0; shift ;;
    -h|--help) usage 0 ;;
    *) echo "未知参数：$1" >&2; usage 2 ;;
  esac
done

find_repo() {
  if [ -n "$REPO_ARG" ]; then printf '%s\n' "$REPO_ARG"; return; fi
  local key
  for key in JEV_REPO JEV_ROUTER_REPO; do
    if [ -n "${!key:-}" ]; then printf '%s\n' "${!key}"; return; fi
  done
  local candidate
  for candidate in \
      "$HOME/AIProjects/agent-labs/jev-codex-router" \
      "$HOME/code/jev-codex-router" \
      "$HOME/src/jev-codex-router" \
      "$HOME/jev-codex-router"; do
    if [ -f "$candidate/server/jev_server.py" ]; then printf '%s\n' "$candidate"; return; fi
  done
  printf '\n'
}

# 补丁是否已经生效：数据文件在，且 routing_policy 真的读它。
has_registry() {
  [ -f "$1/server/route_models.json" ] &&
    grep -q 'route_models.json' "$1/server/routing_policy.py" 2>/dev/null
}

report_state() {
  local repo="$1"
  if has_registry "$repo"; then
    echo "  registry 支持 : 已就绪"
  else
    echo "  registry 支持 : 缺失（需要打补丁）"
  fi
}

verify_repo() {
  local repo="$1" rc=0
  python3 - "$repo" <<'PY' || rc=1
import json, sys
from pathlib import Path
repo = Path(sys.argv[1])
registry = json.loads((repo / "server" / "route_models.json").read_text(encoding="utf-8"))
models = registry.get("models") or []
fallback = (registry.get("fallback") or {}).get("model")
if not models:
    raise SystemExit("route_models.json 里没有候选")
ids = {m.get("id") for m in models}
if fallback not in ids:
    raise SystemExit("fallback {} 不在候选里".format(fallback))
frontier = [m.get("id") for m in models if m.get("frontier")]
if len(frontier) != 1:
    raise SystemExit("frontier 标记应恰好一个，实际 {}".format(frontier))
print("  route_models.json : {} 个候选，fallback={}，frontier={}".format(
    len(models), fallback, frontier[0]))
PY
  if ! python3 "$repo/server/manage_route_models.py" list >/dev/null 2>&1; then
    echo "  manage_route_models.py list : 失败" >&2
    rc=1
  else
    echo "  manage_route_models.py list : OK"
  fi
  if [ "$RUN_TESTS" = "1" ]; then
    if (cd "$repo" && python3 -m unittest discover -s server -p 'test_*.py' >/dev/null 2>&1); then
      echo "  server 测试 : OK"
    else
      echo "  server 测试 : 失败（在 $repo 里跑 python3 -m unittest discover -s server -p 'test_*.py' 看细节）" >&2
      rc=1
    fi
  fi
  return "$rc"
}

# --repo 最高优先级；给了 --clone 就以它为目标（不存在则留空，交给下面的 clone 分支），
# 不能再回退到自动探测，否则会误用本机已有的 checkout。
if [ -n "$REPO_ARG" ]; then
  REPO="$REPO_ARG"
elif [ -n "$CLONE_ARG" ]; then
  if [ -f "$CLONE_ARG/server/jev_server.py" ]; then REPO="$CLONE_ARG"; else REPO=""; fi
else
  REPO="$(find_repo)"
fi

if [ "$CHECK_ONLY" = "1" ]; then
  if [ -z "$REPO" ] || [ ! -f "$REPO/server/jev_server.py" ]; then
    echo "没找到 Jev checkout；用 --repo 指定，或加 --clone <路径> 拉一份。"
    exit 1
  fi
  echo "Jev 仓库：$REPO"
  report_state "$REPO"
  has_registry "$REPO" && exit 0 || exit 1
fi

if [ -z "$REPO" ] || [ ! -f "$REPO/server/jev_server.py" ]; then
  if [ -z "$CLONE_ARG" ]; then
    cat >&2 <<EOF
没找到 Jev checkout。两种做法：
  bash scripts/bootstrap_jev_repo.sh --clone <路径>     # 拉上游 + 打补丁
  bash scripts/bootstrap_jev_repo.sh --repo <已有路径>  # 给已有 checkout 打补丁
EOF
    exit 1
  fi
  if [ -e "$CLONE_ARG" ] && [ -n "$(ls -A "$CLONE_ARG" 2>/dev/null || true)" ]; then
    echo "目标路径已存在且非空：$CLONE_ARG" >&2
    exit 1
  fi
  echo "clone 上游 $UPSTREAM_URL"
  mkdir -p "$(dirname "$CLONE_ARG")"
  git clone "$UPSTREAM_URL" "$CLONE_ARG"
  git -C "$CLONE_ARG" checkout -q "$REF_ARG"
  echo "  已切到 $REF_ARG"
  REPO="$CLONE_ARG"
fi

if [ ! -d "$REPO/.git" ]; then
  echo "$REPO 不是 git 仓库；补丁需要 git 来做三方合并。先 clone 或用 --clone。" >&2
  exit 1
fi

if has_registry "$REPO"; then
  echo "Jev 仓库：$REPO"
  echo "  已经是打过补丁的 checkout，跳过写入。"
else
  if [ ! -f "$PATCH_ARG" ]; then
    echo "补丁不存在：$PATCH_ARG" >&2
    exit 1
  fi
  dirty="$(git -C "$REPO" status --porcelain | grep -v '^??' || true)"
  if [ -n "$dirty" ] && [ "$FORCE" != "1" ]; then
    echo "$REPO 里有未提交改动，先提交/暂存，或加 --force：" >&2
    printf '%s\n' "$dirty" >&2
    exit 1
  fi
  echo "Jev 仓库：$REPO"
  echo "  应用补丁 $(basename "$PATCH_ARG")"
  if git -C "$REPO" apply --3way "$PATCH_ARG" 2>/dev/null; then
    echo "  git apply --3way 成功"
  elif git -C "$REPO" apply "$PATCH_ARG" 2>/dev/null; then
    echo "  git apply 成功"
  elif (cd "$REPO" && patch -p1 --forward <"$PATCH_ARG" >/dev/null); then
    echo "  patch -p1 成功"
  else
    echo "补丁应用失败；checkout 可能不是 ${PINNED_REF}，或有冲突需要手工处理。" >&2
    exit 1
  fi
fi

echo "校验："
verify_repo "$REPO"
echo "完成。后续步骤用 JEV_REPO=${REPO}，或把它写进 shell 配置。"
