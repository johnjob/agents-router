#!/usr/bin/env python3
"""Add, replace, or remove a Jev routing candidate without editing source.

    python3 server/manage_route_models.py list
    python3 server/manage_route_models.py add opencode-go-glm-5-3 --label glm \
        --glyph "✨" --profile "GLM-5.3 via opencode Go." --effort-map medium=high,xhigh=max
    python3 server/manage_route_models.py replace aihubmix-deepseek-v4-1-flash \
        --label glm --glyph "✨"
    python3 server/manage_route_models.py remove gpt-6-astra
    python3 server/manage_route_models.py set-fallback --model gpt-6-astra --effort medium

The registry is server/route_models.json; routing_policy.py reads it. Every
mutation backs the file up, runs the server test suite, restarts the service,
and health-checks it. Use --dry-run / --no-test / --no-restart to opt out.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
REPO_DIR = SERVER_DIR.parent
DEFAULT_DATA_FILE = Path(os.environ.get("JEV_ROUTE_MODELS")
                         or SERVER_DIR / "route_models.json")
DEFAULT_LABEL = os.environ.get("JEV_ROUTER_LABEL", "com.thibaultsaintjean.jev-router")
HEALTH_URL = "http://127.0.0.1:4319/health"


def load_registry(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    payload.setdefault("version", 1)
    payload.setdefault("efforts", [])
    payload.setdefault("models", [])
    payload.setdefault("fallback", {})
    return payload


def seed_registry():
    """Build the registry from the policy the process is currently running."""
    sys.path.insert(0, str(SERVER_DIR))
    import routing_policy as policy  # imported here so --help works anywhere

    return {
        "version": 1,
        "notes": "Jev normal-route candidates. Edited by manage_route_models.py.",
        "policy_version": policy.POLICY_VERSION,
        "efforts": list(policy.EFFORTS),
        "fallback": {"model": policy.FALLBACK_MODEL, "effort": policy.FALLBACK_EFFORT},
        "models": [
            {
                "id": model["id"],
                "alias": model["alias"],
                "label": model["label"],
                "glyph": model["glyph"],
                "profile": model["profile"],
                **({"effort_map": model["effort_map"]} if model["effort_map"] else {}),
            }
            for model in policy.ROUTE_MODELS
        ],
    }


def load_or_seed(path):
    if Path(path).exists():
        return load_registry(path), False
    return seed_registry(), True


def validate(registry):
    models = registry.get("models") or []
    efforts = [str(item).strip() for item in registry.get("efforts") or []]
    if not models:
        raise ValueError("registry needs at least one route model")
    if not efforts:
        raise ValueError("registry needs at least one effort")
    if len(set(efforts)) != len(efforts):
        raise ValueError("efforts must be unique")

    seen = set()
    for model in models:
        model_id = str(model.get("id") or "").strip()
        if not model_id:
            raise ValueError("every model needs a non-empty id")
        if model_id in seen:
            raise ValueError("duplicate model id: {}".format(model_id))
        seen.add(model_id)
        if not str(model.get("glyph") or "").strip():
            raise ValueError("{} needs a glyph".format(model_id))
        effort_map = model.get("effort_map") or {}
        if not isinstance(effort_map, dict):
            raise ValueError("{} effort_map must be an object".format(model_id))
        for source, target in effort_map.items():
            if source not in efforts:
                raise ValueError(
                    "{} maps unknown effort {}; add it to efforts first".format(model_id, source))
            if not str(target or "").strip():
                raise ValueError("{} maps {} to an empty rung".format(model_id, source))

    fallback = registry.get("fallback") or {}
    fallback_model = str(fallback.get("model") or "").strip()
    if fallback_model and fallback_model not in seen:
        raise ValueError("fallback model {} is not a route model".format(fallback_model))
    fallback_effort = str(fallback.get("effort") or "").strip()
    if fallback_effort and fallback_effort not in efforts:
        raise ValueError("fallback effort {} is not in efforts".format(fallback_effort))
    frontier_ids = [model["id"] for model in models if model.get("frontier")]
    if len(frontier_ids) != 1:
        raise ValueError(
            "exactly one model must be the frontier tier (set-frontier MODEL); found: {}".format(
                ", ".join(frontier_ids) or "none"))
    return registry


def _entry(model_id, label, glyph, profile, alias, effort_map, frontier=False):
    entry = {
        "id": model_id,
        "alias": alias or "",
        "label": label or model_id.split("/")[-1],
        "glyph": glyph or "⚡",
        "profile": profile or "",
    }
    if frontier:
        entry["frontier"] = True
    if effort_map:
        entry["effort_map"] = dict(effort_map)
    return entry


def add_model(registry, entry, position=None):
    if any(model.get("id") == entry["id"] for model in registry["models"]):
        raise ValueError("{} already exists; use replace".format(entry["id"]))
    if position is None or position >= len(registry["models"]):
        registry["models"].append(entry)
    else:
        registry["models"].insert(max(position, 0), entry)
    return registry


def replace_model(registry, model_id, entry, position=None):
    for index, model in enumerate(registry["models"]):
        if model.get("id") != model_id:
            continue
        kept_alias = model.get("alias") if not entry.get("alias") else entry["alias"]
        replacement = dict(entry)
        replacement["alias"] = kept_alias
        if model.get("frontier") and not replacement.get("frontier"):
            replacement["frontier"] = True
        if position is None:
            registry["models"][index] = replacement
        else:
            registry["models"].pop(index)
            registry["models"].insert(max(position, 0), replacement)
        if registry.get("fallback", {}).get("model") == model_id:
            registry["fallback"]["model"] = replacement["id"]
        return registry
    raise ValueError("{} is not a route model; use add".format(model_id))


def remove_model(registry, model_id):
    removed = next((model for model in registry["models"] if model.get("id") == model_id), {})
    remaining = [model for model in registry["models"] if model.get("id") != model_id]
    if len(remaining) == len(registry["models"]):
        raise ValueError("{} is not a route model".format(model_id))
    if not remaining:
        raise ValueError("refusing to remove the last route model")
    registry["models"] = remaining
    if registry.get("fallback", {}).get("model") == model_id:
        registry["fallback"]["model"] = remaining[0]["id"]
    if removed.get("frontier") and not any(model.get("frontier") for model in remaining):
        remaining[0]["frontier"] = True
    return registry


def set_frontier(registry, model_id):
    """Mark exactly one model as the frontier tier (the codex-dry GLM target)."""
    if model_id not in {model.get("id") for model in registry["models"]}:
        raise ValueError("{} is not a route model".format(model_id))
    for model in registry["models"]:
        if model.get("id") == model_id:
            model["frontier"] = True
        else:
            model.pop("frontier", None)
    return registry


def set_fallback(registry, model, effort=None):
    ids = {item.get("id") for item in registry["models"]}
    if model not in ids:
        raise ValueError("{} is not a route model".format(model))
    registry.setdefault("fallback", {})["model"] = model
    if effort:
        registry["fallback"]["effort"] = effort
    return registry


def parse_effort_map(raw):
    mapping = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("effort map entries look like medium=high,xhigh=max")
        source, target = item.split("=", 1)
        mapping[source.strip()] = target.strip()
    return mapping


def run_tests():
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "server", "-p", "test_*.py"],
        cwd=str(REPO_DIR), capture_output=True, text=True, check=False,
    )


def restart_service():
    system = platform.system()
    if system == "Darwin":
        target = "gui/{}/{}".format(os.getuid(), DEFAULT_LABEL)
        return subprocess.run(["launchctl", "kickstart", "-k", target],
                              capture_output=True, text=True, check=False)
    if system == "Linux":
        probe = subprocess.run(["systemctl", "--user", "show-environment"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return subprocess.run(["systemctl", "--user", "restart", "jev-router"],
                                  capture_output=True, text=True, check=False)
    raise RuntimeError("no launchd/systemd service found; restart the Jev server yourself")


def wait_healthy(timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as resp:
                if json.loads(resp.read().decode("utf-8", "replace")).get("ok"):
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(1)
    return False


def describe(registry):
    lines = ["efforts: {}".format(", ".join(registry["efforts"]))]
    fallback = registry.get("fallback") or {}
    lines.append("fallback: {} @ {}".format(fallback.get("model"), fallback.get("effort")))
    for index, model in enumerate(registry["models"]):
        effort_map = model.get("effort_map") or {}
        fold = "  map={}".format(
            ",".join("{}->{}".format(k, v) for k, v in effort_map.items())) if effort_map else ""
        frontier = "  frontier" if model.get("frontier") else ""
        lines.append("{:>2}. {:<34} {:<10} {}{}{}".format(
            index, model.get("id"), model.get("label") or "", model.get("glyph") or "",
            fold, frontier))
    lines.append("route pairs: {}".format(len(registry["models"]) * len(registry["efforts"])))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="list",
                        choices=["list", "seed", "add", "replace", "remove",
                                 "set-fallback", "set-frontier"])
    parser.add_argument("model_id", nargs="?",
                        help="model id for add/replace/remove/set-frontier")
    parser.add_argument("--data-file", default=str(DEFAULT_DATA_FILE))
    parser.add_argument("--label")
    parser.add_argument("--glyph")
    parser.add_argument("--profile")
    parser.add_argument("--alias")
    parser.add_argument("--effort-map", help="medium=high,xhigh=max")
    parser.add_argument("--frontier", action="store_true",
                        help="add/replace: mark this model as the frontier tier")
    parser.add_argument("--position", type=int)
    parser.add_argument("--model", help="set-fallback: model id")
    parser.add_argument("--effort", help="set-fallback: effort")
    parser.add_argument("--json", action="store_true", help="list: print raw JSON")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-test", action="store_true", help="skip the server test suite")
    parser.add_argument("--no-restart", action="store_true", help="write only; do not restart")
    args = parser.parse_args()

    path = Path(args.data_file)

    if args.command == "list":
        registry, _seeded = load_or_seed(path)
        validate(registry)
        print(json.dumps(registry, ensure_ascii=False, indent=2) if args.json else describe(registry))
        return 0

    registry, seeded = load_or_seed(path)
    if seeded:
        print("note: {} did not exist; seeded from the running policy".format(path))

    if args.command == "seed":
        validate(registry)
        if args.dry_run:
            print(describe(registry))
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote {}".format(path))
        return 0

    effort_map = parse_effort_map(args.effort_map)
    try:
        if args.command in ("add", "replace"):
            if not args.model_id:
                raise ValueError("{} needs a model id".format(args.command))
            entry = _entry(args.model_id, args.label, args.glyph, args.profile,
                           args.alias, effort_map, frontier=args.frontier)
            if args.command == "add":
                add_model(registry, entry, args.position)
            else:
                replace_model(registry, args.model_id, entry, args.position)
        elif args.command == "set-frontier":
            if not args.model_id:
                raise ValueError("set-frontier needs a model id")
            set_frontier(registry, args.model_id)
        elif args.command == "remove":
            if not args.model_id:
                raise ValueError("remove needs a model id")
            remove_model(registry, args.model_id)
        else:
            target = args.model or args.model_id
            if not target:
                raise ValueError("set-fallback needs --model")
            set_fallback(registry, target, args.effort)
        validate(registry)
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    print(describe(registry))
    if args.dry_run:
        print("\n(dry-run; nothing written)")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name("{}.bak-{}-before-{}".format(
        path.name, stamp, args.command.replace("-", "_")))
    if path.exists():
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\nwrote {} (backup: {})".format(path, backup if path.exists() else "-"))

    if not args.no_test:
        result = run_tests()
        if result.returncode != 0:
            if backup.exists():
                shutil.copy2(backup, path)
                print("server tests failed; restored {}".format(path), file=sys.stderr)
            print((result.stdout or "")[-1500:], file=sys.stderr)
            print((result.stderr or "")[-1500:], file=sys.stderr)
            return 1
        print("server tests: OK")

    if args.no_restart:
        print("skip restart; the running server keeps the previous registry until it restarts")
        return 0

    try:
        result = restart_service()
    except RuntimeError as exc:
        print("warning: {}（新表会在下次启动时生效）".format(exc), file=sys.stderr)
        return 0
    if result.returncode != 0:
        print("restart failed: {}".format((result.stderr or result.stdout or "").strip()[:300]),
              file=sys.stderr)
        return 1
    print("service restarted; health: {}".format("ok" if wait_healthy() else "not healthy yet"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
