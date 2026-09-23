# 平台与路径矩阵

集成涉及两层：**Codex Router**（路由进程 + provider 凭据 + 生成的目录）和
**Codex 客户端**（桌面端或 CLI）。两层在不同系统上的形态不一样，先看这张表再动手。

## 系统差异

| 维度 | macOS | Linux | Windows |
|---|---|---|---|
| Codex 客户端 | 桌面端（`ChatGPT.app` 或 `Codex.app`，两者内都带 Codex framework）+ CLI | 一般只有 CLI | 桌面端 + CLI |
| Codex Router 安装目录 | `~/.local/share/codex-router` | `~/.local/share/codex-router` | `%LOCALAPPDATA%\codex-router` |
| 路由器可执行 | `bin/codex-router` | `bin/codex-router` | `bin\codex-router.cmd` / `.ps1` |
| 状态目录 | `$CODEX_HOME/codex-router` | 同左 | 同左 |
| Jev 常驻方式 | launchd（`~/Library/LaunchAgents`） | systemd user unit | 计划任务 / 启动项（或 WSL 内用 systemd） |
| 路由器服务 | launchd label `io.github.codex-router` | systemd unit | 计划任务 |
| Jev 服务 label | `com.thibaultsaintjean.jev-router` | `jev-router.service` | `JevCodexRouter` |
| Jev 日志 | `~/Library/Logs/jev-router.{out,err}.log` | `journalctl --user -u jev-router` | 计划任务输出文件 |
| 典型坑 | python.org 版 Python 无 CA bundle，launchd 不继承 shell 环境 | systemd user 未必可用（容器/WSL） | 没有 launchd/systemd，别照搬 macOS 步骤 |

Codex 桌面端在 macOS 上的主进程名可能是 `ChatGPT.app/Contents/MacOS/ChatGPT`，
所以**不要用进程名判断**，用完整 bundle 路径。

## 路径与环境变量

优先级：环境变量 > 默认位置。

| 变量 | 含义 | 默认 |
|---|---|---|
| `CODEX_HOME` | Codex 状态根 | `~/.codex` |
| `MODEL_ROUTER_STATE_DIR` | Codex Router 状态目录 | `$CODEX_HOME/codex-router` |
| `CODEX_ROUTER_SOURCE_ROOT` | 路由器安装根目录 | 可执行文件所在目录的上级 |
| `CODEX_ROUTER_BIN` | 直接指定 `codex-router` 可执行文件 | 从 PATH / 常见目录探测 |
| `MODEL_ROUTER_TARGET` | 集成目标客户端 | `codex` |
| `JEV_REPO` | Jev 仓库 checkout 路径（必须是**打过补丁**的 checkout，用 `scripts/bootstrap_jev_repo.sh` 准备；裸 clone 上游缺候选池文件） | 脚本探测 |
| `JEV_ROUTER_LABEL` | macOS launchd label | `com.thibaultsaintjean.jev-router` |

端口固定在本机回环：

| 端口 | 作用 |
|---|---|
| 4200 | 内部 litellm 网关 |
| 4202 | Codex Router 的 caller edge（Codex 实际连这里） |
| 4319 | Jev server（`jev/auto`） |

## 密钥存放位置

不要复制密钥；只要知道它们在哪，判断“是否已配置”时读这些文件的存在性即可。

| 密钥 | 存放位置 | 权限 |
|---|---|---|
| provider API key（aihubmix/ccsub…） | `$CODEX_HOME/codex-router/generic-provider-credentials/<provider>.key` | 600 |
| provider 凭据元数据 | `$CODEX_HOME/codex-router/provider-credentials.json` | 600 |
| `TYPESAFE_API_KEY` | `~/.hermes/.env` → `~/.jev.env` → 进程环境（按此顺序） | 600 |
| caller secret（本机调用边） | `$CODEX_HOME/codex-router/caller-secret` | 600 |

录入一律走 `scripts/configure_secrets.sh`：provider key 交给 Codex Router 自带的
隐藏输入，TypeSafe key 用隐藏输入写 env 文件。

## 安装 Codex Router（未安装时）

让用户在自己的终端执行官方安装器（它会交互式问 provider，不要代为输入密钥）：

macOS / Linux：

```sh
curl -fsSL https://raw.githubusercontent.com/duolahypercho/codex-router/main/install.sh \
  | sh -s -- --target codex --guided
```

Windows：

```powershell
$installer = Join-Path $env:TEMP "codex-router-install.ps1"
Invoke-WebRequest https://raw.githubusercontent.com/duolahypercho/codex-router/main/install.ps1 -OutFile $installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -Target codex -Guided
```

装完先跑 `codex-router doctor` 和 `codex-router status --json`，再继续后续步骤。

## 受管环境

在 agent 沙箱或受管环境里，`launchctl` / `systemctl --user` 常被限制。这时：

1. 不要反复重试，也不要伪造服务；
2. 用 `setup_jev_service.sh --print-only` 打印 unit/plist 给用户；
3. 让用户在自己的终端执行一次；
4. 退路是 `server/watchdog.sh` + cron（每 5 分钟），效果等价于 keep-alive。
