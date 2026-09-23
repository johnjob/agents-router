# 排障

## 模型在界面上只有两档（例如只有「轻度 / 高」）

现象：模型目录里明明声明了 `low/high/max`，选择器只显示两档，缺「最高」。

原因：Codex 客户端渲染档位时会把模型声明的档位与本地设置
`enabled-reasoning-efforts` 求交集，该设置默认是
`low/medium/high/xhigh/ultra`（新版还含 `persistent`），**不含 `max`**。

处理：

```bash
bash scripts/enable_max_effort.sh
```

注意这份状态由运行中的桌面端保存在内存里并整体回写：运行期间直接改
`$CODEX_HOME/.codex-global-state.json` 会被覆盖，所以脚本在 macOS 上会先退出
App、写入、再重开。纯 CLI 用户没有这个控件，不需要处理。

验证：`python3 scripts/verify_stack.py` 里「客户端 max 档」应为 PASS。

## 模型不在选择器里

1. `python3 scripts/calibrate_models.py --list` 确认条目存在；
2. `codex-router refresh-catalog`；
3. `bin/control picker set <slug> show`；
4. **完全退出并重开** Codex 客户端（桌面端只关窗口不重载目录）。

## 真实请求 401 / `{"detail":"Unauthorized"}`

原生 GPT 会话共享过期：

```bash
codex-router chatgpt-session enable
```

## 每轮都路由到 astra

查 `$CODEX_HOME/codex-router/jev-router-live.jsonl` 的 `gate` 字段：

- `jev-router.off` 存在 → 处于 kill switch，删掉即可恢复；
- `no_key_or_task` / `jev_error` → `TYPESAFE_API_KEY` 没读到或已失效；
- `402` → TypeSafe 额度用尽，属预期，tandem 会继续服务。

## 服务装不上

- macOS 提示 `launchctl` 被拒：在**用户自己的终端**里重跑
  `bash <repo>/server/install-service.sh`；
- Linux 没有 systemd --user（容器/WSL）：用
  `*/5 * * * * <repo>/server/watchdog.sh`；
- 任何平台都可以先 `bash scripts/setup_jev_service.sh --print-only` 打印配置再人工落地。

## https 调用证书失败（多见于 macOS 的 python.org 版 Python）

launchd/systemd 不继承 shell 环境，服务里没有 `SSL_CERT_FILE` 就会校验失败。
`setup_jev_service.sh` 和仓库的 `install-service.sh` 会自动探测常见 CA bundle
（`/etc/ssl/cert.pem`、Homebrew 的 `ca-certificates`）并写进服务定义。

## `invalid_responses_response` / “unavailable right now”

转发层把响应当 JSON 解析了。Jev server 对流式回复强制
`Content-Type: text/event-stream`，确认跑的是当前 `server/jev_server.py`。

## `codex-router doctor` 报 Node 版本

Codex Router 要求 Node ≥ 22.19。升级 node 后重启路由器服务；这是环境问题，
不影响 provider/模型配置。

## 怎么快速确认“配置真的生效”

```bash
python3 scripts/detect_env.py            # 环境全貌
python3 scripts/verify_stack.py          # 目录/凭据/服务/档位
python3 scripts/verify_stack.py --live   # 再发一次真实路由请求
```

`--live` 会消耗极少额度，并检查决策日志是否新增一行。

决策日志是**缓冲写**的：服务进程保持文件打开，少量请求可能还停在用户态缓冲区里，
`tail` 看不到、文件 mtime 也不动，直到缓冲区满或进程退出才落盘。判断“路由是否生效”
以 `--live` 从 SSE 里读到的实际 model 为准，日志只作旁证，不要因为它滞后就判定失败。
