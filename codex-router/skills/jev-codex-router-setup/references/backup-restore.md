# 改动前备份与还原

这套 skill 会往 Codex 里写东西，所以**在任何写入之前先留一份基线快照**。装完不满意、
或者只想试试效果，可以整体还原回改动之前。

## 快照覆盖什么

| 受管对象 | 路径 | 谁改它 |
|---|---|---|
| Codex 主配置 | `${CODEX_HOME:-~/.codex}/config.toml` | Codex Router（`codex-router-managed` 区块） |
| 桌面端状态 | `${CODEX_HOME}/.codex-global-state.json` | `enable_max_effort.sh`（`enabled-reasoning-efforts`） |
| Codex Router 状态目录 | `${CODEX_HOME}/codex-router/` | provider 注册、密钥引用、`user-models.json`、picker、目录产物 |
| 密钥文件 | `~/.jev.env`、`~/.hermes/.env` | `configure_secrets.sh`（`TYPESAFE_API_KEY`） |
| 常驻服务定义 | `~/Library/LaunchAgents/<label>.plist`、`~/.config/systemd/user/jev-router.service` | `setup_jev_service.sh` |

快照**不含**：运行日志、决策/用量事件流（`*.log`、`*.jsonl`）、provider 目录缓存、
`__pycache__`、以及状态目录里已有的历史 `backups/`。这些要么可再生，要么只增不减，
放进快照只会让体积膨胀。

Jev 仓库源码（`route_models.json` 等）不在这里，它是你自己的 checkout；
`manage_jev_tiers.sh` 每次写入前会单独备份 `route_models.json.bak-<时间戳>`。

## 打快照

```bash
# 推荐时机：探测完环境、还没注册 provider 之前
bash scripts/backup_codex_state.sh --label pre-integration

bash scripts/backup_codex_state.sh --no-secrets   # 不想把密钥复制进来
bash scripts/backup_codex_state.sh --list         # 看已有快照
```

快照落在 `${CODEX_HOME}/codex-router-backups/<时间戳>-<标签>/`：

```
20260923-142417-pre-integration/
├── MANIFEST.json          # 每个受管对象的路径、是否存在、大小、sha256
└── files/                 # 绝对路径镜像，例如 files/Users/you/.codex/config.toml
```

`MANIFEST.json` 记录「当时不存在」的对象（比如第一次装之前根本没有 `~/.jev.env`），
还原时据此判断哪些文件是集成过程新建的。

**快照包含密钥文件时按 600 权限保存**，脚本只打印路径和大小，从不打印内容。别把
`codex-router-backups/` 提交进版本库或放到共享目录；用 `--no-secrets` 可以只备份配置。

## 还原

```bash
# 1. 先看会做什么（默认就是预览，不写文件）
bash scripts/restore_codex_state.sh --snapshot latest

# 2. 确认后应用
bash scripts/restore_codex_state.sh --snapshot latest --apply

# 3. 连同「集成时新建、当时并不存在」的对象一起清掉
bash scripts/restore_codex_state.sh --snapshot latest --apply --prune
```

行为：

- 默认预览；`--apply` 才写入。写入前会先给**当前**状态再打一份 `pre-restore` 快照，
  所以还原本身也能回退（`--no-pre-snapshot` 跳过）。
- 文件类对象逐字节校验 sha256，不一致会报出来。
- 还原的 `.env` 会被重新置为 600。
- `--prune` 才会移除「快照时不存在、现在存在」的受管对象（例如集成时新建的
  `~/.jev.env`）。不加就只报告，不动它们。
- 还原**不会**删除 Codex Router 状态目录里的其它文件：日志、缓存、历史备份按设计
  不进快照，也就不会被误判成多余文件清掉。

还原之后配置不会立刻生效：要重启 Codex Router，并**完全退出再打开**桌面端
（它会把内存里的旧状态整体回写，不退出的话改动会被覆盖）。最后用
`python3 scripts/verify_stack.py` 复查一遍。

## 和脚本自带备份的关系

两层备份互补，都在，不用二选一：

| 层级 | 位置 | 粒度 |
|---|---|---|
| 全局基线 | `codex-router-backups/<时间戳>-<标签>/` | 装之前 → 装之后整体还原 |
| 单次写入 | `route_models.json.bak-<时间戳>-before-<动作>`、`<状态目录>/backups/<时间戳>-before-calibrate/`、`.codex-global-state.json.bak-<时间戳>-before-enable-max-effort` | 某一个文件的上一版 |
