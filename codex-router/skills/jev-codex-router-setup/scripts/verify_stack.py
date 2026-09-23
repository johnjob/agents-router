#!/usr/bin/env python3
"""端到端校验 Jev Codex Router 集成。只读，除 --live 会发一次很小的真实请求。

    python3 verify_stack.py
    python3 verify_stack.py --live
    python3 verify_stack.py --providers aihubmix,jev

FAIL 会以非零码退出；WARN 只提示。任何情况下都不打印密钥内容。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = SKILL_DIR / "references" / "calibration.json"
JEV_PORT = 4319
ROUTER_PORT = 4202


class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, name, detail=""):
        self.rows.append((level, name, detail))
        print("[{}] {}{}".format(level, name, "  — " + detail if detail else ""))

    def ok(self, name, detail=""):
        self.add("PASS", name, detail)

    def warn(self, name, detail=""):
        self.add("WARN", name, detail)

    def fail(self, name, detail=""):
        self.add("FAIL", name, detail)

    @property
    def failed(self):
        return [row for row in self.rows if row[0] == "FAIL"]


def state_dir():
    override = os.environ.get("MODEL_ROUTER_STATE_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "codex-router"


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def count_lines(path):
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def router_bin():
    override = os.environ.get("CODEX_ROUTER_BIN")
    if override and Path(override).exists():
        return override
    found = shutil.which("codex-router")
    if found:
        return found
    for root in (os.environ.get("CODEX_ROUTER_SOURCE_ROOT"), str(Path.home() / ".local" / "share" / "codex-router")):
        if not root:
            continue
        binary = Path(root) / "bin" / "codex-router"
        if binary.exists():
            return str(binary)
    return None


def run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def http_json(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def check_router(report, binary):
    if not binary:
        report.fail("Codex Router 已安装", "找不到 codex-router 可执行文件")
        return False
    proc = run([binary, "status", "--json"])
    if proc is None or proc.returncode != 0:
        report.fail("Codex Router 服务", "status 命令失败")
        return False
    merged = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                merged.update(json.loads(line))
            except ValueError:
                continue
    state = merged.get("state")
    if state == "running":
        report.ok("Codex Router 服务", "state=running, version={}".format(merged.get("version")))
        return True
    report.fail("Codex Router 服务", "state={}".format(state))
    return False


def check_providers(report, binary, wanted):
    proc = run([binary, "providers", "generic", "list", "--json"]) if binary else None
    if proc is None or proc.returncode != 0:
        report.fail("provider 列表", "providers generic list 失败")
        return
    try:
        providers = json.loads(proc.stdout).get("providers", [])
    except ValueError:
        report.fail("provider 列表", "返回不是 JSON")
        return
    by_id = {p.get("id"): p for p in providers}
    for provider in wanted:
        entry = by_id.get(provider)
        if entry is None:
            report.fail("provider {}".format(provider), "未注册")
        elif not entry.get("enabled"):
            report.fail("provider {}".format(provider), "已注册但未启用")
        else:
            report.ok("provider {}".format(provider), "enabled, adapter={}".format(entry.get("adapter")))
    return by_id


def check_credentials(report, binary, by_id, wanted):
    for provider in wanted:
        entry = (by_id or {}).get(provider) or {}
        if provider == "jev" or entry.get("allowPrivate"):
            continue
        proc = run([binary, "providers", "generic", "credential", provider, "status"])
        if proc is not None and proc.returncode == 0:
            report.ok("{} 凭据".format(provider), "已配置")
        else:
            report.fail("{} 凭据".format(provider), "未配置（让用户跑 configure_secrets.sh）")


def check_typesafe(report, state):
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").parent
    for candidate in (home / ".hermes" / ".env", home / ".jev.env"):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "TYPESAFE_API_KEY" in text:
            report.ok("TYPESAFE_API_KEY", str(candidate))
            return
    report.fail("TYPESAFE_API_KEY", "未在 ~/.hermes/.env 或 ~/.jev.env 找到")


def check_jev(report):
    health = http_json("http://127.0.0.1:{}/health".format(JEV_PORT))
    if health and health.get("ok"):
        report.ok("Jev 服务", "version={}".format(health.get("version")))
    else:
        report.fail("Jev 服务", "127.0.0.1:{} /health 未通过".format(JEV_PORT))


def expected_models(spec, wanted):
    expected = {}
    for provider in wanted:
        group = spec.get("providers", {}).get(provider)
        if not group:
            continue
        defaults = group.get("defaults") or {}
        for raw in group.get("models") or []:
            entry = dict(defaults)
            entry.update(raw)
            expected["{}/{}".format(provider, entry["upstreamModel"])] = entry
    return expected


def check_catalog(report, state, expected):
    catalog = read_json(state / "merged-models.json")
    if not catalog:
        report.fail("模型目录", "读不到 merged-models.json；跑 refresh-catalog")
        return
    entries = {m.get("slug"): m for m in catalog.get("models", [])}
    for slug, want in expected.items():
        got = entries.get(slug)
        if got is None:
            report.fail("目录 {}".format(slug), "不在 merged-models.json")
            continue
        levels = [level.get("effort") for level in got.get("supported_reasoning_levels") or []]
        want_levels = [level.get("effort") for level in want.get("reasoningLevels") or []]
        problems = []
        if levels != want_levels:
            problems.append("档位 {} != {}".format(levels, want_levels))
        if got.get("default_reasoning_level") != want.get("defaultEffort"):
            problems.append("默认档 {} != {}".format(got.get("default_reasoning_level"), want.get("defaultEffort")))
        if got.get("context_window") != want.get("contextWindow"):
            problems.append("上下文 {} != {}".format(got.get("context_window"), want.get("contextWindow")))
        if problems:
            report.fail("目录 {}".format(slug), "; ".join(problems))
        else:
            report.ok("目录 {}".format(slug), "ctx={}, levels={}".format(want.get("contextWindow"), ",".join(want_levels)))


def check_picker(report, state, expected):
    picker = read_json(state / "model-picker.json")
    if picker is None:
        report.warn("picker 状态", "读不到 model-picker.json")
        return
    visible = set(picker.get("visible") or [])
    missing = [slug for slug in expected if slug not in visible]
    if missing:
        report.warn("picker 可见性", "未显示：{}（跑 control picker set <slug> show）".format(", ".join(missing)))
    else:
        report.ok("picker 可见性", "{} 个模型全部可见".format(len(expected)))


def check_max_effort(report):
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    document = read_json(home / ".codex-global-state.json")
    if not document:
        report.warn("客户端 max 档", "没有桌面端状态文件（纯 CLI 不需要）")
        return
    atoms = document.get("electron-persisted-atom-state") or {}
    levels = atoms.get("enabled-reasoning-efforts")
    if levels and "max" in levels:
        report.ok("客户端 max 档", "enabled-reasoning-efforts 含 max")
    else:
        report.warn("客户端 max 档", "不含 max：声明 low/high/max 的模型只会显示两档（跑 enable_max_effort.sh）")


def find_jev_repo():
    candidates = []
    for key in ("JEV_REPO", "JEV_ROUTER_REPO"):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]))
    candidates.append(Path.cwd())
    for name in ("jev-codex-router",):
        for base in (Path.home() / "AIProjects" / "agent-labs", Path.home() / "code",
                     Path.home() / "src", Path.home()):
            candidates.append(base / name)
    for path in candidates:
        if (path / "server" / "jev_server.py").is_file():
            return path
    return None


def check_route_models(report, state):
    """Every Jev candidate must resolve to a model the catalog actually serves.

    This is the guard for the documented order "add the downstream model first,
    remove it last": a candidate whose gateway model disappeared would fail at
    request time, not at startup.
    """
    repo = find_jev_repo()
    if not repo:
        report.warn("Jev 候选可解析", "没找到 Jev 仓库（设 JEV_REPO）；跳过")
        return
    override = os.environ.get("JEV_ROUTE_MODELS")
    registry_path = Path(override) if override else repo / "server" / "route_models.json"
    registry = read_json(registry_path)
    if registry is None:
        report.warn("Jev 候选可解析", "读不到 {}".format(registry_path))
        return

    # The caller edge resolves the catalog slug -- the id Codex itself sends.
    # An entry written as the internal gateway id (aihubmix-deepseek-v4-1-flash)
    # falls through to the native edge and 400s, so accept slugs only.
    known = set()
    for model in (read_json(state / "merged-models.json") or {}).get("models", []) or []:
        if model.get("slug"):
            known.add(model["slug"])

    candidates = [model.get("id") for model in registry.get("models", []) or []]
    fallback = (registry.get("fallback") or {}).get("model")
    missing = [item for item in candidates + ([fallback] if fallback else []) if item not in known]
    if missing:
        report.fail("Jev 候选可解析",
                    "目录里找不到这些 gateway：{}（先加下游模型再换候选）".format(", ".join(missing)))
    else:
        report.ok("Jev 候选可解析",
                  "{} 个候选 + fallback 都在目录里".format(len(candidates)))


def live_probe(report, state):
    secret_path = state / "caller-secret"
    try:
        secret = secret_path.read_text(encoding="utf-8").strip()
    except OSError:
        report.fail("真实路由", "读不到 caller-secret")
        return
    if not secret:
        report.fail("真实路由", "caller-secret 为空")
        return

    log_path = state / "jev-router-live.jsonl"
    before = count_lines(log_path)

    url = "http://127.0.0.1:{}/_codex-router/{}/v1/responses".format(ROUTER_PORT, secret)
    body = {
        "model": "jev/auto",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Say OK"}]}],
        "stream": True,
    }
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    collected = b""
    deadline = time.time() + 150
    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            while time.time() < deadline and len(collected) < 200_000:
                chunk = resp.read(4096)
                if not chunk:
                    break
                collected += chunk
                if b"[DONE]" in chunk or b"response.completed" in chunk:
                    break
    except urllib.error.HTTPError as error:
        report.fail("真实路由", "HTTP {}: {}".format(error.code, error.read()[:200].decode("utf-8", "replace")))
        return
    except (urllib.error.URLError, OSError) as error:
        report.fail("真实路由", "请求失败：{}".format(error))
        return

    text = collected.decode("utf-8", "replace")
    if "response.created" in text or "data:" in text:
        match = re.search(r'"model"\s*:\s*"([^"]+)"', text)
        if match:
            report.ok("真实路由", "收到 SSE 流；路由到 {}".format(match.group(1)))
        else:
            report.ok("真实路由", "收到 SSE 流")
    else:
        report.fail("真实路由", "响应不像 SSE：{}".format(text[:160]))
        return

    # 服务进程可能缓冲日志，写入会滞后；轮询一段时间再判断。
    after = before
    for _ in range(20):
        time.sleep(1)
        after = count_lines(log_path)
        if after > before:
            break
    if log_path.exists():
        if after > before:
            last = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
            try:
                record = json.loads(last)
                report.ok("决策日志", "gate={}, tier={}, model={}, effort={}, status={}".format(
                    record.get("gate"), record.get("tier"), record.get("model"),
                    record.get("effort"), record.get("status")))
            except ValueError:
                report.ok("决策日志", "新增 1 行")
        else:
            report.warn("决策日志", "20 秒内没有新增行；服务可能仍在缓冲，稍后看 {}".format(log_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument("--providers", default="aihubmix,ccsub,jev")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--live", action="store_true", help="发一次很小的真实路由请求（消耗少量额度）")
    args = parser.parse_args()

    state = Path(args.state_dir) if args.state_dir else state_dir()
    wanted = [p.strip() for p in args.providers.split(",") if p.strip()]
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    expected = expected_models(spec, wanted)

    report = Report()
    print("CODEX_HOME 状态目录：{}\n".format(state))

    binary = router_bin()
    check_router(report, binary)
    by_id = check_providers(report, binary, wanted) if binary else {}
    if binary:
        check_credentials(report, binary, by_id, wanted)
    if "jev" in wanted:
        check_typesafe(report, state)
        check_jev(report)
    check_catalog(report, state, expected)
    check_picker(report, state, expected)
    check_route_models(report, state)
    check_max_effort(report)
    if args.live:
        print("\n--live：发送一次很小的真实请求 --")
        live_probe(report, state)

    failed = report.failed
    print("\n结果：{} 项通过，{} 项警告，{} 项失败".format(
        sum(1 for row in report.rows if row[0] == "PASS"),
        sum(1 for row in report.rows if row[0] == "WARN"),
        len(failed),
    ))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
