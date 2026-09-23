#!/usr/bin/env python3
"""把校准表合并进 Codex Router 的 user-models.json（幂等，可回滚）。

    python3 calibrate_models.py --list
    python3 calibrate_models.py --providers aihubmix --dry-run
    python3 calibrate_models.py --providers aihubmix,ccsub,jev --apply

临时增删某个 provider 的模型（不改校准表）：

    python3 calibrate_models.py --providers aihubmix --discover
    python3 calibrate_models.py --providers aihubmix --add glm-5.2 \
        --ctx 1048576 --levels low,high,max --default max --apply
    python3 calibrate_models.py --providers aihubmix --remove glm-5.2 --apply

只用 references/calibration.json 当数据源；只覆盖校准字段（上下文窗口、
autoCompact、思考档位、默认档、输入模态），已有条目的展示名/描述/优先级保持
不变。写入前备份到 <state-dir>/backups/<时间戳>-before-calibrate/。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = SKILL_DIR / "references" / "calibration.json"

CALIBRATION_FIELDS = ("defaultEffort", "reasoningLevels", "contextWindow", "autoCompact", "inputModalities")
EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "persistent"]


def state_dir():
    override = os.environ.get("MODEL_ROUTER_STATE_DIR")
    if override:
        return Path(override)
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return codex_home / "codex-router"


def gateway_safe(value):
    text = re.sub(r"[^a-z0-9-]+", "-", str(value).lower())
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def model_identity(provider, upstream):
    gateway = "{}-{}".format(gateway_safe(provider), gateway_safe(upstream))
    return {
        "slug": "{}/{}".format(provider, upstream),
        "gatewayModel": gateway,
        "compHash": "{}-user-v1".format(gateway),
        "upstreamModel": upstream,
        "provider": provider,
        "listed": True,
    }


def validate(provider, entry):
    upstream = entry.get("upstreamModel")
    if not upstream:
        raise SystemExit("[{}] 缺少 upstreamModel".format(provider))
    levels = [level.get("effort") for level in entry.get("reasoningLevels") or []]
    if not levels:
        raise SystemExit("[{}] {} 缺少 reasoningLevels".format(provider, upstream))
    unknown = [level for level in levels if level not in EFFORT_ORDER]
    if unknown:
        raise SystemExit(
            "[{}] {} 的思考档位 {} 不在 Codex 的取值范围内".format(provider, upstream, unknown)
        )
    if entry.get("defaultEffort") not in levels:
        raise SystemExit(
            "[{}] {} 的 defaultEffort={} 不在档位 {} 里".format(
                provider, upstream, entry.get("defaultEffort"), levels
            )
        )
    context = entry.get("contextWindow")
    auto = entry.get("autoCompact")
    if not isinstance(context, int) or not isinstance(auto, int) or context <= 0 or auto <= 0:
        raise SystemExit("[{}] {} 的 contextWindow/autoCompact 必须是正整数".format(provider, upstream))
    if auto > context:
        raise SystemExit("[{}] {} 的 autoCompact 不能大于 contextWindow".format(provider, upstream))
    if not entry.get("inputModalities"):
        raise SystemExit("[{}] {} 缺少 inputModalities".format(provider, upstream))


def load_spec(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    providers = data.get("providers") or {}
    resolved = {}
    for provider, group in providers.items():
        defaults = group.get("defaults") or {}
        models = []
        for raw in group.get("models") or []:
            entry = dict(defaults)
            entry.update(raw)
            validate(provider, entry)
            models.append(entry)
        resolved[provider] = {"group": group, "models": models}
    return resolved


def load_user_models(path):
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("models"), list):
            raise SystemExit("{} 结构异常：缺少 models 数组".format(path))
        return data
    return {"version": 1, "models": []}


def snapshot(entry):
    return {field: entry.get(field) for field in CALIBRATION_FIELDS}


def plan(current, spec, providers):
    existing = {}
    for model in current["models"]:
        if isinstance(model, dict) and model.get("slug"):
            existing[model["slug"]] = model

    updates = []
    additions = []
    for provider in providers:
        group = spec[provider]
        for entry in group["models"]:
            identity = model_identity(provider, entry["upstreamModel"])
            slug = identity["slug"]
            target = existing.get(slug)
            if target is None:
                additions.append((provider, entry, identity))
            else:
                before = snapshot(target)
                after = {field: entry.get(field) for field in CALIBRATION_FIELDS}
                if before != after:
                    updates.append((provider, slug, before, after))
    return updates, additions


def apply_plan(current, spec, providers):
    by_slug = {}
    for provider in providers:
        for entry in spec[provider]["models"]:
            identity = model_identity(provider, entry["upstreamModel"])
            by_slug[identity["slug"]] = (provider, entry, identity)

    models = []
    for model in current["models"]:
        slug = model.get("slug") if isinstance(model, dict) else None
        if slug in by_slug:
            _, entry, _ = by_slug[slug]
            for field in CALIBRATION_FIELDS:
                model[field] = entry.get(field)
        models.append(model)

    present = {model.get("slug") for model in models}
    for slug, (provider, entry, identity) in by_slug.items():
        if slug in present:
            continue
        new_entry = dict(identity)
        new_entry["displayName"] = entry.get("displayName") or slug
        new_entry["description"] = entry.get("description") or "User-curated {} model.".format(provider)
        new_entry["priority"] = entry.get("priority", 100)
        for field in CALIBRATION_FIELDS:
            new_entry[field] = entry.get(field)
        models.append(new_entry)

    current["models"] = models
    return current


def find_router_bin():
    override = os.environ.get("CODEX_ROUTER_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("codex-router")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("CODEX_ROUTER_SOURCE_ROOT", "")) if os.environ.get("CODEX_ROUTER_SOURCE_ROOT") else None,
        Path.home() / ".local" / "share" / "codex-router",
    ):
        if not candidate:
            continue
        binary = candidate / "bin" / "codex-router"
        if binary.exists():
            return str(binary)
    return None


def control_bin(router):
    if not router:
        return None
    for name in ("control", "control.cmd", "control.ps1"):
        candidate = Path(router).parent / name
        if candidate.exists():
            return str(candidate)
    return None


def discover(router, provider):
    """Ask Codex Router which models this provider exposes and which are curated."""
    if not router:
        raise SystemExit("找不到 codex-router；先安装或用 CODEX_ROUTER_BIN 指定")
    proc = subprocess.run([router, "discover-models", provider],
                          capture_output=True, text=True, check=False)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        return proc.returncode
    return 0


def split_list(raw):
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def ad_hoc_entry(model_id, defaults, args):
    """Build one registry entry from CLI flags, falling back to provider defaults."""
    context = args.ctx or defaults.get("contextWindow")
    if not context:
        raise SystemExit("--ctx 必填（该 provider 在校准表里没有默认上下文窗口）")
    auto = args.auto or int(context * 0.85)
    levels = split_list(args.levels) or [
        level.get("effort") for level in defaults.get("reasoningLevels") or []
    ] or ["high"]
    default = args.default_effort or defaults.get("defaultEffort") or (
        "high" if "high" in levels else levels[-1])
    if default not in levels:
        raise SystemExit("--default {} 不在 --levels {} 里".format(default, ",".join(levels)))
    return {
        "upstreamModel": model_id,
        "displayName": args.display_name or "{} (curated)".format(model_id),
        "description": args.description or "User-curated model added by calibrate_models.py.",
        "priority": args.priority if args.priority is not None else 100,
        "defaultEffort": default,
        "reasoningLevels": [{"effort": level, "description": "Declared by the operator"}
                            for level in levels],
        "contextWindow": context,
        "autoCompact": auto,
        "inputModalities": split_list(args.modalities) or defaults.get("inputModalities") or ["text"],
    }


def hide_in_picker(router, slugs):
    """Best effort: drop removed models from the picker so the UI does not list a
    slug the catalog no longer serves."""
    control = control_bin(router)
    if not control or not Path(control).exists():
        return
    for slug in slugs:
        subprocess.run([control, "picker", "set", slug, "hide"],
                       capture_output=True, text=True, check=False)


def reload_router(router):
    """重启运行中的 Codex Router，让新写的 user-models.json 真正生效。

    refresh-catalog 只重写目录文件；model-registry.mjs 在进程启动时建表，运行中
    的路由器不会自己重读，所以新增/移除的模型必须靠一次服务重启才会变成可路由。
    """
    control = control_bin(router)
    if not control or not Path(control).exists():
        print("未找到 control，跳过路由器重载；请手动重启 Codex Router。")
        return 0
    try:
        proc = subprocess.run([control, "service", "restart"],
                              capture_output=True, text=True, check=False, timeout=900)
    except subprocess.TimeoutExpired:
        print("路由器重载超时；请检查服务日志。")
        return 1
    if proc.returncode != 0:
        print("路由器重载失败：{}".format((proc.stderr or proc.stdout).strip()[:400]))
        return proc.returncode
    print("路由器已重启，新模型表已生效。")
    return 0


def publish_and_reload(router, args):
    """先重建目录，再让运行中的路由器加载新表。"""
    if not router:
        print("未找到 codex-router，跳过 refresh-catalog 与重载；稍后手动执行。")
        return 0
    proc = subprocess.run([router, "refresh-catalog"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print("refresh-catalog 失败：{}".format((proc.stderr or proc.stdout).strip()[:400]))
        return proc.returncode
    print("refresh-catalog 完成。")
    if args.no_reload:
        print("已按 --no-reload 跳过路由器重载；运行中的路由仍是旧表。")
        return 0
    return reload_router(router)


def fmt_value(value):
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return describe_levels(value)
        return ",".join(str(item) for item in value)
    return str(value)


def describe_levels(value):
    return ",".join(level.get("effort", "?") for level in value or [])


def print_diff(updates, additions):
    for provider, slug, before, after in updates:
        changed = [field for field in CALIBRATION_FIELDS if before.get(field) != after.get(field)]
        print("更新 {} [{}]".format(slug, ", ".join(changed)))
        for field in changed:
            if field == "reasoningLevels":
                old_levels = describe_levels(before.get(field))
                new_levels = describe_levels(after.get(field))
                if old_levels == new_levels:
                    print("    reasoningLevels  档位未变，仅档位描述更新：{}".format(new_levels))
                else:
                    print("    reasoningLevels  {} -> {}".format(old_levels, new_levels))
                continue
            print("    {:<16} {} -> {}".format(field, fmt_value(before.get(field)), fmt_value(after.get(field))))
    for provider, entry, identity in additions:
        print("新增 {} (ctx={}, autoCompact={}, default={}, levels={})".format(
            identity["slug"], entry.get("contextWindow"), entry.get("autoCompact"),
            entry.get("defaultEffort"), fmt_value(entry.get("reasoningLevels")),
        ))
    if not updates and not additions:
        print("已是最新，无需改动。")


def print_current(models):
    for model in models:
        levels = [level.get("effort") for level in model.get("reasoningLevels") or []]
        print("{:<34} ctx={:>9} auto={:>9} default={:<6} levels={}".format(
            model.get("slug"), model.get("contextWindow"), model.get("autoCompact"),
            model.get("defaultEffort"), ",".join(levels),
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(DEFAULT_SPEC), help="校准表路径")
    parser.add_argument("--providers", default="", help="逗号分隔；默认取校准表里的全部 provider")
    parser.add_argument("--apply", action="store_true", help="写入（默认只做 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="只打印差异")
    parser.add_argument("--no-refresh", action="store_true", help="写入后不调用 refresh-catalog")
    parser.add_argument("--list", action="store_true", help="打印当前 user-models.json 表")
    parser.add_argument("--state-dir", default=None, help="覆盖 Codex Router 状态目录")
    parser.add_argument("--discover", action="store_true", help="列出该 provider 已选/可选的模型")
    parser.add_argument("--add", default="", help="临时加入模型 id（逗号分隔）")
    parser.add_argument("--remove", default="", help="移除模型 id（逗号分隔）")
    parser.add_argument("--ctx", type=int, help="--add 的上下文窗口")
    parser.add_argument("--auto", type=int, help="--add 的 autoCompact（默认取 ctx 的 0.85 倍）")
    parser.add_argument("--levels", help="--add 的档位，例如 low,high,max")
    parser.add_argument("--default", dest="default_effort", help="--add 的默认档")
    parser.add_argument("--modalities", help="--add 的输入模态，默认 text")
    parser.add_argument("--display-name", help="--add 的展示名")
    parser.add_argument("--description", help="--add 的描述")
    parser.add_argument("--priority", type=int, help="--add 的排序优先级")
    parser.add_argument("--keep-picker", action="store_true", help="移除时不隐藏 picker 条目")
    parser.add_argument("--no-reload", action="store_true",
                        help="只重建目录，不重启 Codex Router（新模型要等它重启才可路由）")
    args = parser.parse_args()

    directory = Path(args.state_dir) if args.state_dir else state_dir()
    models_path = directory / "user-models.json"

    if args.list:
        print_current(load_user_models(models_path).get("models", []))
        return 0

    spec = load_spec(args.spec)
    requested = [p.strip() for p in args.providers.split(",") if p.strip()] or list(spec.keys())
    unknown = [p for p in requested if p not in spec]
    if unknown:
        raise SystemExit("校准表里没有 provider：{}".format(", ".join(unknown)))
    if (args.add or args.remove) and len(requested) != 1:
        raise SystemExit("--add/--remove 一次只能针对一个 provider，当前：{}".format(", ".join(requested)))

    router = find_router_bin()
    provider = requested[0] if len(requested) == 1 else None
    current = load_user_models(models_path)

    if args.discover:
        return discover(router, requested[0])

    if args.add:
        defaults = spec[provider]["group"].get("defaults") or {}
        for model_id in split_list(args.add):
            entry = ad_hoc_entry(model_id, defaults, args)
            spec[provider]["models"] = [
                model for model in spec[provider]["models"]
                if model.get("upstreamModel") != model_id
            ]
            spec[provider]["models"].append(entry)

    if args.remove:
        targets = {"{}/{}".format(provider, model_id) for model_id in split_list(args.remove)}
        removed = [model.get("slug") for model in current.get("models", [])
                   if model.get("slug") in targets]
        print("数据源 : {}".format(args.spec))
        print("目标   : {}".format(models_path))
        print("移除   : {}".format(", ".join(removed) if removed else "无匹配条目"))
        if not removed:
            print("(slug 不匹配；先跑 --list 看实际 slug)")
            return 0
        if not args.apply or args.dry_run:
            print("\n(dry-run；加 --apply 才会写入)")
            return 0
        current["models"] = [model for model in current["models"]
                             if model.get("slug") not in targets]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = directory / "backups" / "{}-before-remove".format(stamp)
        backup_dir.mkdir(parents=True, exist_ok=True)
        if models_path.exists():
            shutil.copy2(models_path, backup_dir / "user-models.json")
        models_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print("\n已写入；备份在 {}".format(backup_dir))
        if not args.keep_picker:
            hide_in_picker(router, removed)
        if args.no_refresh:
            return 0
        return publish_and_reload(router, args)

    updates, additions = plan(current, spec, requested)
    print("数据源 : {}".format(args.spec))
    print("目标   : {}".format(models_path))
    print("provider: {}".format(", ".join(requested)))
    print_diff(updates, additions)

    if not args.apply or args.dry_run:
        print("\n(dry-run；加 --apply 才会写入)")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = directory / "backups" / "{}-before-calibrate".format(stamp)
    backup_dir.mkdir(parents=True, exist_ok=True)
    if models_path.exists():
        shutil.copy2(models_path, backup_dir / "user-models.json")

    updated = apply_plan(current, spec, requested)
    models_path.parent.mkdir(parents=True, exist_ok=True)
    models_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n已写入；备份在 {}".format(backup_dir))

    if args.no_refresh:
        return 0
    return publish_and_reload(router, args)


if __name__ == "__main__":
    sys.exit(main())
