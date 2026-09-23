# codex-router 集成

让 Codex 通过 Codex Router 使用外部模型，并由 `jev/auto` 自动路由。

## 目录结构

| 路径 | 内容 |
|---|---|
| `skills/jev-codex-router-setup/` | 可安装的 skill：注册 provider、录入密钥、校准上下文与思考档位、装常驻服务、端到端验证，以及日常增删模型 |
| `src/jev-codex-router/` | 上游源码快照，已包含本集成需要的改动 |

## 上游与补丁

上游是第三方开源项目 `0xNatoshi/jev-codex-router`（MIT），**它本身没有**本集成依赖的
候选池功能。补丁固定基于 commit `faf46df90f3c6a3962bb5876d393c7d6552b7c8f`。

- 补丁文件：`skills/jev-codex-router-setup/assets/patches/0001-jev-registry-and-hardening.patch`
- 补丁说明与更新方式：`skills/jev-codex-router-setup/references/repo-patch.md`

补丁带来三样东西，缺了就会在「日常变更模型」那一步失败：

1. `server/route_models.json` 与 `server/manage_route_models.py`：候选池的数据文件和增删改工具；
2. `routing_policy.py` 的 registry 支持（`FALLBACK_MODEL`、`FRONTIER_MODEL`、
   `ROUTE_LABELS`、`EFFORT_MAPS`、`provider_effort_for`）；
3. 两处服务加固：launchd 的 `SSL_CERT_FILE`、`JEV_API_URL` 可覆盖。

`src/jev-codex-router/` 就是打完补丁后的状态，可以直接读，也可以当作打补丁失败时的对照。
它由「上游已跟踪文件 + 4 个新增源码文件」组成，不含 `.git`、运行日志和备份文件。

## 使用

```bash
# 推荐：让 skill 自己拉上游并打补丁（幂等，可重复跑）
bash skills/jev-codex-router-setup/scripts/bootstrap_jev_repo.sh \
  --clone ~/AIProjects/agent-labs/jev-codex-router

# 或者：直接用这份快照
cp -R src/jev-codex-router ~/AIProjects/agent-labs/jev-codex-router
```

之后按 `SKILL.md` 的第 2 步往下走，最后用真实请求验证：

```bash
python3 skills/jev-codex-router-setup/scripts/verify_stack.py --live
```

## 改动前先快照，随时能还原

集成会改 Codex 的配置，所以 skill 的第 0 步就是留一份基线快照：

```bash
bash skills/jev-codex-router-setup/scripts/backup_codex_state.sh --label pre-integration
```

覆盖 `~/.codex/config.toml`、桌面端状态、`~/.codex/codex-router/` 状态目录、
`~/.jev.env` 与 `~/.hermes/.env`、常驻服务定义。想整体退回：

```bash
bash skills/jev-codex-router-setup/scripts/restore_codex_state.sh --snapshot latest          # 预览
bash skills/jev-codex-router-setup/scripts/restore_codex_state.sh --snapshot latest --apply  # 执行
```

还原前会自动给当前状态再打一份 `pre-restore` 快照，所以这一步本身也能回退。细节见
`skills/jev-codex-router-setup/references/backup-restore.md`。

## 已知限制

- 桌面端只在启动时读一次模型目录，新增模型后要**完全退出再打开**才可见；
- 客户端默认的 `enabled-reasoning-efforts` 不含 `max`，声明多档的模型界面上会少一档，
  需要跑一次 `scripts/enable_max_effort.sh`（它会退出并重开桌面端）；
- 候选池默认包含走 AIHubMix 的模型，没配 `AIHUBMIX_API_KEY` 时这些候选会失败。
