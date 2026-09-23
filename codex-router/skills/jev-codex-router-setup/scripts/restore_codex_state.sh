#!/usr/bin/env bash
# 用 backup_codex_state.sh 的快照把 Codex 配置还原回改动之前。
#
#   bash scripts/restore_codex_state.sh --list
#   bash scripts/restore_codex_state.sh --snapshot latest              # 只预览
#   bash scripts/restore_codex_state.sh --snapshot latest --apply      # 真正还原
#   bash scripts/restore_codex_state.sh --snapshot <名字> --apply --prune
#
# 默认只预览，不写任何文件。--apply 之前会先给「当前状态」打一份 pre-restore 快照，
# 所以还原本身也能再回退（--no-pre-snapshot 可跳过）。
# --prune 会移除「快照里不存在、但现在已经出现」的受管对象，例如集成时新建的
# ~/.jev.env 或 .hermes/.env；不加就只报告、不动它们。
# 注意：还原会覆盖受管文件，但不会删除 codex-router 状态目录里的其它文件
# （日志、缓存、历史备份按设计不进快照，也就不会被当成多余文件清掉）。
set -euo pipefail

CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
BACKUP_ROOT="$CODEX_HOME_DIR/codex-router-backups"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SNAPSHOT="latest"
APPLY=0
PRUNE=0
PRE_SNAPSHOT=1
LIST_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --snapshot) SNAPSHOT="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --prune) PRUNE=1; shift ;;
    --no-pre-snapshot) PRE_SNAPSHOT=0; shift ;;
    --list) LIST_ONLY=1; shift ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [ ! -d "$BACKUP_ROOT" ]; then
  echo "没有快照目录（$BACKUP_ROOT）。先跑 backup_codex_state.sh。" >&2
  exit 1
fi

if [ "$LIST_ONLY" = "1" ]; then
  echo "快照目录：$BACKUP_ROOT"
  for entry in "$BACKUP_ROOT"/*/; do
    [ -d "$entry" ] || continue
    echo "  - $(basename "$entry")"
  done
  exit 0
fi

if [ "$SNAPSHOT" = "latest" ]; then
  # 目录名以时间戳开头，字典序即时间序。
  RESOLVED="$(ls -1 "$BACKUP_ROOT" 2>/dev/null | grep -E '^[0-9]{8}-[0-9]{6}-' | sort | tail -n 1 || true)"
  if [ -z "$RESOLVED" ]; then
    echo "找不到任何快照。" >&2
    exit 1
  fi
else
  RESOLVED="$SNAPSHOT"
fi
SNAP="$BACKUP_ROOT/$RESOLVED"
if [ ! -f "$SNAP/MANIFEST.json" ]; then
  echo "快照不存在或缺少 MANIFEST.json：$SNAP" >&2
  exit 1
fi

run_restore() {  # run_restore <mode: plan|apply> [will-apply]
  python3 - "$SNAP" "$1" "$PRUNE" "${2:-0}" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path

snap, mode, prune = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"
will_apply = sys.argv[4] == "1"
doc = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
entries = doc.get("entries") or []

def home_short(path):
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path

to_restore, absent_now, to_remove = [], [], []
for item in entries:
    target = Path(item["path"])
    src = snap / item["snapshotPath"]
    if item.get("present"):
        to_restore.append((item, src, target))
    elif target.exists():
        (to_remove if prune else absent_now).append((item, target))

if mode == "plan":
    print("快照：{}（标签 {}，平台 {}）".format(snap.name, doc.get("label"), doc.get("platform")))
    if doc.get("secretsIncluded"):
        print("这份快照包含密钥文件。")
    print()
    print("将还原（{} 个）：".format(len(to_restore)))
    for item, _src, target in to_restore:
        if item.get("kind") == "dir":
            extra = "（{} 个文件）".format(item.get("files"))
        elif item.get("bytes") is not None:
            extra = "（{} 字节）".format(item["bytes"])
        else:
            extra = ""
        print("  {} {}".format(home_short(str(target)), extra))
    if to_remove:
        print()
        print("将移除（快照时不存在，--prune 生效）：")
        for _item, target in to_remove:
            print("  {}".format(home_short(str(target))))
    elif absent_now:
        print()
        print("快照时不存在、但现在存在（默认不动，加 --prune 才移除）：")
        for _item, target in absent_now:
            print("  {}".format(home_short(str(target))))
    if not will_apply:
        print()
        print("这是预览；加 --apply 才会写入。")
    raise SystemExit(0)

restored, failed = 0, []
for item, src, target in to_restore:
    try:
        if item.get("kind") == "dir":
            target.mkdir(parents=True, exist_ok=True)
            for path in src.rglob("*"):
                if path.is_dir():
                    continue
                dest = target / path.relative_to(src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            if item["path"].endswith(".env"):
                os.chmod(target, 0o600)
        if item.get("sha256"):
            if hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]:
                failed.append((str(target), "校验不一致"))
        restored += 1
    except Exception as exc:  # noqa: BLE001 - 报错要带上路径
        failed.append((str(target), str(exc)))

removed = 0
for _item, target in to_remove:
    try:
        shutil.rmtree(target) if target.is_dir() else target.unlink()
        removed += 1
    except Exception as exc:  # noqa: BLE001
        failed.append((str(target), str(exc)))

print("已还原 {} 个对象{}。".format(restored, "，移除 {} 个".format(removed) if removed else ""))
for path, why in failed:
    print("  失败 {}：{}".format(path, why))
raise SystemExit(1 if failed else 0)
PY
}

run_restore plan "$APPLY"

if [ "$APPLY" != "1" ]; then
  exit 0
fi

if [ "$PRE_SNAPSHOT" = "1" ]; then
  echo
  echo "先给当前状态打一份 pre-restore 快照（还原本身也能回退）..."
  if ! bash "$SKILL_DIR/scripts/backup_codex_state.sh" --label pre-restore >/dev/null; then
    echo "pre-restore 快照失败；为安全起见中止还原。" >&2
    exit 1
  fi
  echo "  已保存（--no-pre-snapshot 可跳过这一步）"
fi

echo
STATUS=0
run_restore apply || STATUS=$?

echo
echo "接下来（配置改动要重启才生效）："
echo "  1) 重启 Codex Router：在安装目录的 bin/ 下跑 control service restart"
echo "  2) 桌面端完全退出再打开（它会把内存里的旧状态整体回写）"
echo "  3) 复查：python3 \"$SKILL_DIR/scripts/verify_stack.py\""
exit "$STATUS"
