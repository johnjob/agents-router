#!/usr/bin/env bash
# 在改动任何 Codex 配置之前，把「现状」整体快照下来，出问题时可以还原回去。
#
#   bash scripts/backup_codex_state.sh                    # 快照，标签 pre-integration
#   bash scripts/backup_codex_state.sh --label before-x    # 自定义标签
#   bash scripts/backup_codex_state.sh --no-secrets        # 不打包密钥文件
#   bash scripts/backup_codex_state.sh --list              # 看已有的快照
#
# 快照落在 ${CODEX_HOME:-~/.codex}/codex-router-backups/<时间戳>-<标签>/，
# 里面是 files/ 下的绝对路径镜像 + MANIFEST.json。还原用 restore_codex_state.sh。
#
# 快照是「原样复制」：config.toml、桌面端状态、Codex Router 状态目录、密钥文件、
# 常驻服务定义都会进来。密钥文件按 600 权限保存，脚本只打印路径和大小，不打印内容。
set -euo pipefail

CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
BACKUP_ROOT="$CODEX_HOME_DIR/codex-router-backups"
SERVICE_LABEL="${JEV_ROUTER_LABEL:-com.thibaultsaintjean.jev-router}"

LABEL="pre-integration"
INCLUDE_SECRETS=1
LIST_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --label) LABEL="${2:-}"; shift 2 ;;
    --no-secrets) INCLUDE_SECRETS=0; shift ;;
    --list) LIST_ONLY=1; shift ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [ "$LIST_ONLY" = "1" ]; then
  if [ ! -d "$BACKUP_ROOT" ]; then
    echo "还没有任何快照（$BACKUP_ROOT 不存在）。"
    exit 0
  fi
  echo "快照目录：$BACKUP_ROOT"
  found=0
  for entry in "$BACKUP_ROOT"/*/; do
    [ -d "$entry" ] || continue
    found=1
    name="$(basename "$entry")"
    summary="$(python3 - "$entry/MANIFEST.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    doc = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
entries = doc.get("entries") or []
present = [e for e in entries if e.get("present")]
print("{} 个受管对象（{} 个存在），密钥：{}".format(
    len(entries), len(present), "含" if doc.get("secretsIncluded") else "不含"))
PY
)"
    echo "  - $name  $summary"
  done
  [ "$found" = "1" ] || echo "  （目录是空的）"
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
SNAP="$BACKUP_ROOT/$STAMP-$LABEL"
if [ -e "$SNAP" ]; then
  echo "快照已存在：$SNAP" >&2
  exit 1
fi
mkdir -p "$SNAP/files"
ENTRIES="$(mktemp "${TMPDIR:-/tmp}/jev-backup-entries.XXXXXX")"
trap 'rm -f "$ENTRIES"' EXIT

# 把绝对路径映射成 files/ 下的镜像路径：/Users/x/.codex/config.toml
# -> files/Users/x/.codex/config.toml
rel_for() {
  printf 'files/%s' "${1#/}"
}

record() {  # record <present|absent> <abs_path> <rel_path>
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >>"$ENTRIES"
}

snap_file() {  # snap_file <abs_path> [mode]
  local src="$1" mode="${2:-}" rel dst
  rel="$(rel_for "$src")"
  dst="$SNAP/$rel"
  if [ -f "$src" ]; then
    mkdir -p "$(dirname "$dst")"
    cp -p "$src" "$dst"
    [ -n "$mode" ] && chmod "$mode" "$dst"
    record present "$src" "$rel"
  else
    record absent "$src" "$rel"
  fi
}

snap_dir() {  # snap_dir <abs_dir>：排除日志、事件流、缓存、历史备份
  local src="$1" rel dst
  rel="$(rel_for "$src")"
  dst="$SNAP/$rel"
  if [ -d "$src" ]; then
    mkdir -p "$dst"
    tar -cf - -C "$src" \
      --exclude='router.log' \
      --exclude='*.log' \
      --exclude='*.jsonl' \
      --exclude='*.bak' \
      --exclude='*.bak-*' \
      --exclude='__pycache__' \
      --exclude='provider-catalog-cache.json' \
      --exclude='backups' \
      . | tar -xf - -C "$dst"
    record present "$src" "$rel"
  else
    record absent "$src" "$rel"
  fi
}

snap_file "$CODEX_HOME_DIR/config.toml"
snap_file "$CODEX_HOME_DIR/.codex-global-state.json"
snap_dir "$CODEX_HOME_DIR/codex-router"

if [ "$INCLUDE_SECRETS" = "1" ]; then
  snap_file "$HOME/.jev.env" 600
  snap_file "$HOME/.hermes/.env" 600
else
  record absent "$HOME/.jev.env" "$(rel_for "$HOME/.jev.env")"
  record absent "$HOME/.hermes/.env" "$(rel_for "$HOME/.hermes/.env")"
fi

snap_file "$HOME/Library/LaunchAgents/$SERVICE_LABEL.plist"
snap_file "$HOME/.config/systemd/user/jev-router.service"

python3 - "$ENTRIES" "$SNAP" "$STAMP" "$LABEL" "$INCLUDE_SECRETS" "$SERVICE_LABEL" <<'PY'
import hashlib, json, os, platform, sys
from pathlib import Path

entries_tsv, snap, stamp, label, secrets_included, service_label = sys.argv[1:7]
snap_path = Path(snap)

entries = []
for line in Path(entries_tsv).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    status, abs_path, rel_path = line.split("\t")
    item = {"path": abs_path, "snapshotPath": rel_path, "present": status == "present"}
    target = snap_path / rel_path
    if item["present"] and target.is_file():
        data = target.read_bytes()
        item["bytes"] = len(data)
        item["sha256"] = hashlib.sha256(data).hexdigest()
    elif item["present"] and target.is_dir():
        files = [p for p in target.rglob("*") if p.is_file()]
        item["kind"] = "dir"
        item["files"] = len(files)
        item["bytes"] = sum(p.stat().st_size for p in files)
    entries.append(item)

manifest = {
    "version": 1,
    "stamp": stamp,
    "label": label,
    "platform": platform.system(),
    "codexHome": str(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")),
    "secretsIncluded": secrets_included == "1",
    "serviceLabel": service_label,
    "entries": entries,
}
snap_path.joinpath("MANIFEST.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

present = [e for e in entries if e["present"]]
print("快照完成：{}".format(snap_path))
print("  受管对象 {} 个，其中存在 {} 个".format(len(entries), len(present)))
for item in entries:
    mark = "有" if item["present"] else "无"
    detail = ""
    if item["present"]:
        detail = "  {} 字节".format(item.get("bytes"))
    print("  [{}] {}{}".format(mark, item["path"], detail))
if manifest["secretsIncluded"]:
    print("  注意：快照包含密钥文件（权限 600）。别把它提交进版本库或放到共享目录。")
print("还原：bash scripts/restore_codex_state.sh --snapshot {} --apply".format(snap_path.name))
PY
