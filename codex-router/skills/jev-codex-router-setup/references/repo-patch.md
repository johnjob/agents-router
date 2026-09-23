# 内置仓库补丁

上游 `0xNatoshi/jev-codex-router` 是一个独立开源项目，**它本身不包含**本 skill 依赖的
候选池功能。裸 clone 的话，前几步能跑通，到“日常变更”就会失败
（`manage_jev_tiers.sh` 找不到 `server/manage_route_models.py`）。

所以 skill 自带一份补丁：`assets/patches/0001-jev-registry-and-hardening.patch`，
由 `scripts/bootstrap_jev_repo.sh` 负责 clone、打补丁、校验。

## 补丁包含什么

| 文件 | 作用 | 不打会怎样 |
|---|---|---|
| `server/route_models.json` | 候选池数据文件（唯一可编辑来源） | 候选池无从谈起 |
| `server/manage_route_models.py` | 候选池增删改工具 | `manage_jev_tiers.sh` 直接报错 |
| `server/routing_policy.py` | 改成从 registry 派生 `TIERS/EFFORTS/FALLBACK_MODEL/FRONTIER_MODEL/ROUTE_LABELS/EFFORT_MAPS`，新增 `provider_effort_for()` | 档位映射、`set-frontier`、`set-fallback` 都不存在 |
| `server/test_route_models.py` 等测试 | registry 的行为约束 | 改候选池后没有回归保护 |
| `server/jev_server.py` | `JEV_API_URL` 可覆盖、按候选映射 provider 档位、`ROUTE_LABELS` | 档位折不到 provider 的阶梯上 |
| `server/install-service.sh` | launchd 里补 `SSL_CERT_FILE` | python.org 版 Python 起服务后 https 证书校验失败 |
| `scripts/enable-max-reasoning-effort.sh` | 打开客户端 max 档的仓库侧脚本 | 与 skill 的 `enable_max_effort.sh` 重复但保留原样 |
| `README.md`、`poc/*`、`.gitignore` | 文档与忽略规则同步 | 无功能影响 |

补丁按上游 commit `faf46df90f3c6a3962bb5876d393c7d6552b7c8f` 生成，脚本也固定 checkout
到这个 commit，避免上游漂移后打不上。

## 怎么用

```bash
# 新机器：拉上游固定 commit + 打补丁 + 跑测试校验
bash scripts/bootstrap_jev_repo.sh --clone ~/AIProjects/agent-labs/jev-codex-router

# 已有 checkout：只打补丁（幂等）
bash scripts/bootstrap_jev_repo.sh --repo /path/to/jev-codex-router

# 只报告状态，不写文件（退出码 0=已就绪，1=缺补丁）
bash scripts/bootstrap_jev_repo.sh --check

# 其他开关：--ref <commit> 换基准、--force 覆盖未提交改动、--no-test 跳过测试
```

判定“已经打好补丁”的两个标记：`server/route_models.json` 存在，且
`server/routing_policy.py` 里出现 `route_models.json`。两个都满足就跳过写入。

checkout 里有**未提交改动**时脚本默认拒绝，避免把别人的在制品覆盖掉；确认要覆盖再加
`--force`。补丁应用优先走 `git apply --3way`，退回 `git apply`，最后退回 `patch -p1`。

## 上游更新后怎么刷新这份补丁

仓库侧改动都提交到 checkout 之后，在仓库根目录重新生成，然后替换 skill 里的文件：

```bash
git -C "$JEV_REPO" add -A
git -C "$JEV_REPO" diff --cached > /tmp/jev-changes.patch
cp /tmp/jev-changes.patch \
  "$HOME/.codex/skills/jev-codex-router-setup/assets/patches/0001-jev-registry-and-hardening.patch"
```

同时把 `scripts/bootstrap_jev_repo.sh` 里的 `PINNED_REF` 改成新的基准 commit，并在干净的
临时目录里验证一遍（clone → 打补丁 → 跑 `server/` 测试）再交付。

注意 `git add -A` 会把运行期产物也纳入 diff（`*.log`、`*.jsonl`、`*.bak-*` 已被
`.gitignore` 覆盖）。生成补丁前先确认 `git status` 里只有源码改动。
