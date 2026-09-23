#!/usr/bin/env python3
"""探测 Jev Codex Router 集成所需的机器环境，输出 JSON。

只读：不写任何文件，不读取密钥值（只判断密钥是否已配置）。

    python3 detect_env.py            # 人类可读摘要
    python3 detect_env.py --json     # 机器可读
    python3 detect_env.py --no-search  # 不在 HOME 里搜索 Jev 仓库
"""

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

JEV_PORT = 4319
ROUTER_PORT = 4202
LITELLM_PORT = 4200
MIN_NODE = (22, 19)
MIN_PYTHON = (3, 11)

SKIP_DIRS = {
    ".git", ".cache", ".local", ".npm", ".nvm", ".cargo", ".rustup", ".venv",
    "venv", "node_modules", "Library", "AppData", ".Trash", "Applications",
    "Pictures", "Music", "Movies", "Downloads",
}


def run(cmd, timeout=20):
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def port_open(port, host="127.0.0.1", timeout=0.7):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def http_json(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def version_tuple(text):
    if not text:
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def state_dir():
    override = os.environ.get("MODEL_ROUTER_STATE_DIR")
    return Path(override) if override else codex_home() / "codex-router"


def router_candidates():
    candidates = []
    if os.environ.get("CODEX_ROUTER_SOURCE_ROOT"):
        candidates.append(Path(os.environ["CODEX_ROUTER_SOURCE_ROOT"]))
    if os.environ.get("CODEX_ROUTER_HOME"):
        candidates.append(Path(os.environ["CODEX_ROUTER_HOME"]))
    on_path = shutil.which("codex-router")
    if on_path:
        candidates.append(Path(on_path).resolve().parent.parent)
    if platform.system() == "Windows":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        candidates.append(Path(local) / "codex-router")
    candidates.append(Path.home() / ".local" / "share" / "codex-router")
    candidates.append(Path.home() / "codex-router")
    return candidates


def router_bin_for(root):
    for name in ("codex-router", "codex-router.cmd", "codex-router.ps1"):
        candidate = root / "bin" / name
        if candidate.exists():
            return candidate
    return None


def find_router():
    for root in router_candidates():
        binary = router_bin_for(root)
        if binary:
            return root, binary
    on_path = shutil.which("codex-router")
    if on_path:
        return Path(on_path).resolve().parent.parent, Path(on_path)
    return None, None


def jev_candidates():
    candidates = []
    for key in ("JEV_REPO", "JEV_ROUTER_REPO"):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]))
    candidates.append(Path.cwd())
    for name in ("jev-codex-router", "codex-router"):
        for base in (Path.home() / "AIProjects" / "agent-labs", Path.home() / "code", Path.home() / "src", Path.home()):
            candidates.append(base / name)
    return candidates


def looks_like_jev_repo(path):
    return (path / "server" / "jev_server.py").is_file()


def search_jev_repo(max_entries=40000, max_depth=4):
    home = Path.home()
    seen = 0
    for root, dirs, files in os.walk(home):
        root_path = Path(root)
        depth = len(root_path.relative_to(home).parts)
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        seen += len(dirs) + len(files)
        if "jev_server.py" in files and root_path.name == "server":
            return root_path.parent
        if seen > max_entries:
            return None
    return None


def find_jev_repo(allow_search=True):
    for path in jev_candidates():
        try:
            if looks_like_jev_repo(path):
                return path
        except OSError:
            continue
    if allow_search:
        return search_jev_repo()
    return None


def desktop_apps():
    found = []
    mac_apps = [
        Path("/Applications/ChatGPT.app"),
        Path("/Applications/Codex.app"),
        Path.home() / "Applications" / "ChatGPT.app",
    ]
    for app in mac_apps:
        binary = app / "Contents" / "Resources" / "codex"
        if app.exists():
            found.append({
                "path": str(app),
                "name": app.stem,
                "bundled_cli": str(binary) if binary.exists() else None,
            })
    return found


def running_app_bundles():
    system = platform.system()
    if system == "Darwin":
        out = run(["ps", "-Ao", "command="]) or ""
        pattern = r"/Applications/[^\s]+\.app/Contents/MacOS/[^\s]+"
        return sorted(set(re.findall(pattern, out)))
    return []


def router_status(binary):
    if not binary:
        return None
    raw = run([str(binary), "status", "--json"])
    if not raw:
        return None
    merged = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            merged.update(json.loads(line))
        except ValueError:
            continue
    return merged or None


def generic_providers(binary):
    if not binary:
        return None
    raw = run([str(binary), "providers", "generic", "list", "--json"])
    if not raw:
        return None
    try:
        return json.loads(raw).get("providers", [])
    except ValueError:
        return None


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def env_file_defines(path, name):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=", text, re.M) is not None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-search", action="store_true", help="不在 HOME 下搜索 Jev 仓库")
    args = parser.parse_args()

    python_version = "{}.{}.{}".format(*sys.version_info[:3])
    node_text = run(["node", "-v"])
    node_ok = False
    if node_text:
        parsed = version_tuple(node_text)
        node_ok = parsed is not None and parsed >= MIN_NODE

    home = codex_home()
    state = state_dir()
    router_root, router_bin = find_router()
    status = router_status(router_bin)
    providers = generic_providers(router_bin)
    jev_repo = find_jev_repo(allow_search=not args.no_search)

    picker = read_json(state / "model-picker.json") or {}
    user_models = read_json(state / "user-models.json") or {}
    model_rows = []
    for model in user_models.get("models", []) or []:
        model_rows.append({
            "slug": model.get("slug"),
            "provider": model.get("provider"),
            "contextWindow": model.get("contextWindow"),
            "autoCompact": model.get("autoCompact"),
            "defaultEffort": model.get("defaultEffort"),
            "levels": [level.get("effort") for level in model.get("reasoningLevels", []) or []],
        })

    global_state = read_json(home / ".codex-global-state.json") or {}
    atoms = global_state.get("electron-persisted-atom-state", {}) or {}
    effort_levels = atoms.get("enabled-reasoning-efforts")

    typesafe_env = None
    for candidate in (home.parent / ".hermes" / ".env", home.parent / ".jev.env"):
        if env_file_defines(candidate, "TYPESAFE_API_KEY"):
            typesafe_env = str(candidate)
            break

    payload = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": python_version,
            "python_ok": sys.version_info[:2] >= MIN_PYTHON,
            "node": node_text,
            "node_ok": node_ok,
            "shell": os.environ.get("SHELL"),
        },
        "paths": {
            "home": str(Path.home()),
            "codex_home": str(home),
            "state_dir": str(state),
            "codex_router_root": str(router_root) if router_root else None,
            "codex_router_bin": str(router_bin) if router_bin else None,
            "control_bin": str(router_root / "bin" / "control") if router_root else None,
            "jev_repo": str(jev_repo) if jev_repo else None,
            "jev_server": str(jev_repo / "server" / "jev_server.py") if jev_repo else None,
            "desktop_state_file": str(home / ".codex-global-state.json"),
            "typesafe_env_file": typesafe_env,
        },
        "clients": {
            "desktop_apps": desktop_apps(),
            "running_app_bundles": running_app_bundles(),
            "codex_cli": shutil.which("codex"),
        },
        "services": {
            "router_status": status,
            "router_port_open": port_open(ROUTER_PORT),
            "litellm_port_open": port_open(LITELLM_PORT),
            "jev_port_open": port_open(JEV_PORT),
            "jev_health": http_json("http://127.0.0.1:{}/health".format(JEV_PORT)),
        },
        "providers": providers,
        "picker_visible": picker.get("visible"),
        "user_models": model_rows,
        "client_effort_setting": {
            "value": effort_levels,
            "max_enabled": bool(effort_levels) and "max" in effort_levels,
        },
        "capabilities": {
            "codex_router_installed": router_bin is not None,
            "codex_router_running": bool(status and status.get("state") == "running"),
            "jev_repo_found": jev_repo is not None,
            "jev_running": bool(http_json("http://127.0.0.1:{}/health".format(JEV_PORT))),
            "generic_providers": [p.get("id") for p in (providers or [])],
            "enabled_providers": [p.get("id") for p in (providers or []) if p.get("enabled")],
            "has_aihubmix_key": bool(providers) and any(
                p.get("id") == "aihubmix" and p.get("credentialRef") for p in providers
            ),
            "has_ccsub_key": bool(providers) and any(
                p.get("id") == "ccsub" and p.get("credentialRef") for p in providers
            ),
            "has_typesafe_key": typesafe_env is not None,
        },
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    p = payload
    print("平台      : {} {} ({})".format(p["platform"]["system"], p["platform"]["release"], p["platform"]["machine"]))
    print("Python    : {} {}".format(p["platform"]["python"], "OK" if p["platform"]["python_ok"] else "需要 >= 3.11"))
    print("Node      : {} {}".format(p["platform"]["node"] or "未找到", "OK" if p["platform"]["node_ok"] else "需要 >= 22.19"))
    print("CODEX_HOME: {}".format(p["paths"]["codex_home"]))
    print("路由器    : {}".format(p["paths"]["codex_router_bin"] or "未安装"))
    print("Jev 仓库  : {}".format(p["paths"]["jev_repo"] or "未找到（设 JEV_REPO）"))
    print("服务      : 4202={} 4319={} jev_health={}".format(
        p["services"]["router_port_open"], p["services"]["jev_port_open"],
        bool(p["services"]["jev_health"])))
    print("Providers : {}".format(", ".join(p["capabilities"]["generic_providers"]) or "无"))
    print("密钥      : aihubmix={} ccsub={} typesafe={}".format(
        p["capabilities"]["has_aihubmix_key"], p["capabilities"]["has_ccsub_key"],
        p["capabilities"]["has_typesafe_key"]))
    print("max 档    : {}".format(p["client_effort_setting"]["max_enabled"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
