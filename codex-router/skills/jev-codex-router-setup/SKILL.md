---
name: jev-codex-router-setup
description: 在用户的机器上安装、配置并校准 Jev Codex Router 集成：注册 Codex Router 外部 provider、安全录入密钥、写入模型上下文与思考档位校准、发布模型目录、按平台安装常驻服务、打开客户端 max 档、端到端验证，以及日常模型变更（换/加 Jev 路由候选、增删某个 provider 的模型）。用于“帮我搭一遍这套路由”“新电脑集成”“把某家模型接进 Codex”“换掉/新增一个路由模型”“密钥我自己输入”这类请求。不用于通用反向代理搭建或模型质量评测。
---

# Jev Codex Router 集成

把 Jev 自动路由（`jev/auto`）和外部模型接进 Codex。目标终态：Codex 模型
选择器里能看到这些模型，`jev/auto` 能端到端跑通，所有密钥只存在于本机受保护
文件里。

## 铁律（不要绕过）

1. **脚本优先。** 每一步都走 `scripts/`。不要手改生成物：`litellm.yaml`、
   `~/.codex/config.toml` 的 `codex-router-managed` 区块、`merged-models.json`。
   只有两份数据文件可以改，而且都要通过脚本：`user-models.json`
   （`scripts/calibrate_models.py`）和仓库内的 `server/route_models.json`
   （`scripts/manage_jev_tiers.sh`）。
2. **密钥只进用户自己的终端。** 任何 key 都不许出现在对话、命令行参数、日志或
   工具输出里。要 key 的步骤一律让用户在**系统终端**里跑
   `scripts/configure_secrets.sh`（内部用隐藏输入）。不要用 `exec_command` 代跑
   密钥脚本，也不要让用户把 key 发给你。密钥脚本不接受 `--key` 之类的参数。
3. **先探测，再动手。** 任何改动前先跑 `scripts/detect_env.py --json`，按结果
   决定分支；不要假设路径、平台或已装组件。
4. **每步之后验证。** 至少跑 `scripts/verify_stack.py` 里对应的检查项。
5. **可回滚、不误删。** 脚本写文件前必须备份。绝不删除用户已有的 provider、
   模型条目或状态文件内容，只做合并式修改。

## 前置条件

- macOS、Linux，或 Windows（原生 PowerShell 或 WSL）。
- Node.js ≥ 22.19（Codex Router 的运行前提）。
- Python ≥ 3.11（Jev server 的运行前提；也是本 skill 脚本的运行前提）。
- Codex 桌面端或 Codex CLI 至少有一个。
- 一个**打好补丁的** Jev 仓库 checkout。上游 `0xNatoshi/jev-codex-router` 本身不够用：
  本 skill 依赖的候选池（`server/route_models.json`、`server/manage_route_models.py`、
  registry 版 `routing_policy.py`）和两处服务加固都在 skill 内置补丁里，裸 clone 会在
  “日常变更”那一步直接失败。用第 1 步的 `scripts/bootstrap_jev_repo.sh` 准备。
- 密钥：`TYPESAFE_API_KEY` 必需；`AIHUBMIX_API_KEY`、`CCSUB_API_KEY` 按需。

平台差异、路径解析和客户端形态见
[references/environment-matrix.md](references/environment-matrix.md)。

## 流程

### 0. 探测环境

```bash
python3 scripts/detect_env.py --json
```

输出里 `capabilities` 标出缺什么、`paths` 给出本机实际路径。缺 Codex Router
或 Node/Python 版本不够时，先按 environment-matrix 里的对应小节引导用户补齐，
不要继续后面的步骤。

### 1. 准备 Jev 仓库（clone + 打补丁）

上游里没有候选池相关文件，本 skill 把它们和两处服务加固打成一个补丁：

```bash
# 本机还没有 checkout：拉上游固定 commit + 打补丁 + 校验
bash scripts/bootstrap_jev_repo.sh --clone ~/AIProjects/agent-labs/jev-codex-router

# 已有 checkout：只打补丁（幂等；有未提交改动会先拒绝，除非 --force）
bash scripts/bootstrap_jev_repo.sh --repo /path/to/jev-codex-router

# 只看状态，不写任何文件
bash scripts/bootstrap_jev_repo.sh --check
```

脚本结束会打印 `JEV_REPO=<路径>`；把它导出到当前 shell，后面所有脚本都靠它定位仓库。
补丁内容和更新方式见 [references/repo-patch.md](references/repo-patch.md)。

### 2. 确认 Codex Router

`detect_env.py` 报 `codex_router.installed = false` 时，让用户按
environment-matrix 里对应平台的官方安装命令自行安装（安装脚本会交互式询问
provider，属于用户操作）。已安装则跳过。

### 3. 注册 provider 描述（不含密钥）

```bash
bash scripts/register_providers.sh --providers aihubmix,ccsub,jev
```

脚本按 provider 逐个 `add`/`edit` 并 `enable`，幂等，可重复执行。它只写描述
（base URL、adapter、allow-private），不碰密钥。

### 4. 由用户录入密钥

把下面这条命令交给用户，让**用户在自己的终端**执行：

```bash
bash scripts/configure_secrets.sh --providers aihubmix,ccsub --typesafe
```

脚本行为：

- provider key 走 Codex Router 自带的隐藏输入（`providers generic credential
  ID set`），密钥落到本机受保护存储；
- `TYPESAFE_API_KEY` 用隐藏输入写入 `~/.jev.env`（权限 600），Jev server
  启动时自己读取；
- 原生 ChatGPT 会话共享走 `codex-router chatgpt-session enable`，在用户终端里
  完成；
- 全程只打印“已配置/跳过”，不打印密钥内容。

脚本结束后让用户回报“完成”，你再继续。如果用户已经配过某个 key，脚本会提示
已存在并允许跳过。

### 5. 校准模型元数据

```bash
python3 scripts/calibrate_models.py --providers aihubmix,ccsub,jev --apply
```

它把校准表合并进 `user-models.json`：上下文窗口、autoCompact、思考档位、
默认档位。先用 `--dry-run` 看 diff 再 `--apply`。唯一数据源是
`references/calibration.json`：换 provider 或新增模型时改它（不要手改
`user-models.json`），证据和口径见
[references/calibration.md](references/calibration.md)。

### 6. 发布目录和 picker

```bash
"$CODEX_ROUTER_BIN" refresh-catalog
"$(dirname "$CODEX_ROUTER_BIN")/control" picker set jev/auto show
```

`register_providers.sh` 和 `calibrate_models.py` 通常已经触发了重发布；这一步
是幂等的兜底。`control` 和 `codex-router` 都在安装目录的 `bin/` 下。

### 7. 常驻服务

```bash
bash scripts/setup_jev_service.sh
```

macOS 走 launchd，Linux 走 systemd user unit，两者都不可用或受限时给出
watchdog + cron 的替代方案。脚本不静默失败：装不上会明确说明原因和替代路径。

### 8. 打开客户端 max 档

```bash
bash scripts/enable_max_effort.sh
```

Codex 客户端默认的 `enabled-reasoning-efforts` 不含 `max`，所以声明了
`low/high/max` 的模型界面上只剩两档。该脚本把 `max` 加进本地设置；macOS 桌面端
必须退出后再写（App 会在内存里整体回写覆盖），脚本会自己完成退出、写入、重开。
纯 CLI 用户不需要这一步。原因和细节见
[references/troubleshooting.md](references/troubleshooting.md)。

### 9. 端到端验证

```bash
python3 scripts/verify_stack.py --live
```

依次检查：两个服务在监听、`jev/auto` 在目录和 picker 里、provider 凭据就绪、
思考档位符合校准、真实路由请求返回 SSE 且决策日志新增一行。任一项失败按
troubleshooting 处理后重跑，不要带着失败项宣布完成。

### 10. 日常变更：换/加路由候选、增删 provider 模型

装好之后最常见的两类变更，都有脚本；不要手改 `routing_policy.py` 或
`user-models.json`。

```bash
# Jev 内部候选：jev/auto 会把每一轮派给这些模型
bash scripts/manage_jev_tiers.sh list
bash scripts/manage_jev_tiers.sh add <gateway-model> --label glm --glyph "✨" \
  --profile "GLM-5.3 via opencode Go." --effort-map medium=high,xhigh=max
bash scripts/manage_jev_tiers.sh replace <gateway-model> --label glm --glyph "✨"
bash scripts/manage_jev_tiers.sh remove <gateway-model>
bash scripts/manage_jev_tiers.sh set-frontier <gateway-model>   # codex-dry 时走 GLM 的那一档

# 某个 provider 可选的模型：先看再增删
python3 scripts/calibrate_models.py --providers aihubmix --discover
python3 scripts/calibrate_models.py --providers aihubmix --add glm-5.2 \
  --ctx 1048576 --levels low,high,max --default max --apply
python3 scripts/calibrate_models.py --providers aihubmix --remove glm-5.2 --apply
```

两条硬规则：`manage_jev_tiers.sh` 的模型 id 用 caller edge 认的目录 slug
（路由模型是 `aihubmix/deepseek-v4.1-flash` 这种 `厂商/模型`，原生模型是
`gpt-5.6-sol` 这种自身 id）；写内部 gateway id（`aihubmix-deepseek-v4-1-flash`）会被
edge 当成不支持的模型直接 400；
变更顺序永远**先加后删**，换完候选先跑 `scripts/verify_stack.py --live` 确认真实
请求走通，再删旧模型。完整约束、回滚和备份位置见
[references/managing-models.md](references/managing-models.md)。

两个命令都会在写入并通过校验后重启各自的服务：Jev 候选重启 Jev server，
provider 模型先 `refresh-catalog` 再重启 Codex Router（路由器在启动时建表，只写文件
不重启会继续用旧表）。桌面端不自动重启，新目录仍需完全退出重开才可见。

### 11. 收尾

告诉用户这几件事，不要让他自己猜：

- 桌面端要**完全退出并重开**才会加载新目录；
- 决策日志 `~/.codex/codex-router/jev-router-live.jsonl`，服务日志在
  environment-matrix 列出的位置；
- 即时开关：`jev-router.off`（跳过 Jev，直接走前沿模型）、
  `jev-router.shadow`（只记录决策不改变路由）；
- 回滚：禁用 provider、停服务、以及本 skill 各脚本自己产生的备份文件。

## 参考文档

- [references/environment-matrix.md](references/environment-matrix.md)：
  平台、路径、客户端形态、服务管理器、日志位置。
- [references/calibration.md](references/calibration.md)：模型校准表与证据。
- [references/managing-models.md](references/managing-models.md)：换/加 Jev 路由
  候选、增删 provider 模型、先加后删的顺序与回滚。
- [references/troubleshooting.md](references/troubleshooting.md)：只显示两档、
  Node 版本、证书、401、会话过期等。
- [references/repo-patch.md](references/repo-patch.md)：内置补丁包含哪些仓库改动、
  怎么更新它、怎么确认某个 checkout 已经打好补丁。
