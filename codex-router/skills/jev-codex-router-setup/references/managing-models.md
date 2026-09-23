# 变更模型

这里有两种“模型”，别混：

| 变更对象 | 影响 | 数据源 | 工具 |
|---|---|---|---|
| Jev 路由候选 | `jev/auto` 内部把每一轮派给哪些模型 | `server/route_models.json`（仓库内） | `scripts/manage_jev_tiers.sh` |
| provider 模型 | 选择器里能直接选的模型、目录和上下文/档位 | `user-models.json`（状态目录） | `scripts/calibrate_models.py` |

两者都遵守同样的顺序：**先加后删**。新模型先在下游（provider 模型）存在，再把它加进
Jev 候选；删候选之前先把新候选加进去，否则 `jev/auto` 会在切换窗口里失败。

两个脚本都会在写入后自动重启**各自**的服务：Jev 候选改的是 Jev server（4319），
provider 模型改的是 Codex Router（4202）。桌面端不在这两个脚本的管理范围内——
它只在启动时读一次模型目录，所以看完新目录要靠**完全退出并重开**。

## A. 换/加/删 Jev 路由候选

`route_models.json` 里的 `id` 必须是 **caller edge 认的模型 id，也就是目录里的 slug**
（Codex 客户端发出去的那个 id）：

- 路由模型写 `厂商/模型`，例如 `aihubmix/deepseek-v4.1-flash`；
- 原生模型写它自己的 id，例如 `gpt-5.6-luna`、`gpt-6-astra`（两者恰好同名）。

写成内部的 gateway id（`aihubmix-deepseek-v4-1-flash`、`ccsub-gpt-6-astra`）会被 edge
当成不认识的模型直接透传到原生边，返回
`The '<id>' model is not supported when using Codex with a ChatGPT account`（400）——
实测过，别用。

```bash
# 看现状（顺序即候选顺序，pairs = 候选数 × 档位数）
bash scripts/manage_jev_tiers.sh list

# 新增一个候选，并把 Jev 的 5 档折到该 provider 实际接受的档位
bash scripts/manage_jev_tiers.sh add <catalog-slug，如 opencode-go/glm-5.3> \
  --label glm --glyph "✨" --profile "GLM-5.3 via opencode Go." \
  --effort-map medium=high,xhigh=max

# 原地替换：entry 由本次传入的 flag 重新拼，没传的字段会回到默认值
# （label 取 id 末段、glyph 取 ⚡、profile 清空），只有 alias 和 frontier 会沿用
# 旧值；所以换字段时把 label/glyph/profile/--effort-map 都显式写全。
# 不带 --position 就保持原来的位置。
bash scripts/manage_jev_tiers.sh replace aihubmix/deepseek-v4.1-flash \
  --label deepseek --glyph "🐳" --profile "DeepSeek V4.1 Flash via AIHubMix." \
  --effort-map medium=high,xhigh=max

# 移除
bash scripts/manage_jev_tiers.sh remove gpt-6-astra

# Jev 失败/kill switch/shadow 时落到哪个模型
bash scripts/manage_jev_tiers.sh set-fallback --model gpt-6-astra --effort medium

# 指定“前沿档”：codex-dry 时这一档走 GLM，也是代码里 ASTRA 常量的取值
bash scripts/manage_jev_tiers.sh set-frontier gpt-5.6-sol
```

行为与约束：

- 写前备份 `route_models.json.bak-<时间戳>-before-<动作>`；
- 写后自动跑 `server/` 测试套件。测试失败会**还原文件**并返回非零；
- 成功后按平台重启服务（macOS launchd / Linux systemd），并做一次健康检查；
- `--effort-map` 的 key 必须是 `efforts` 里已有的档位；value 是该 provider 声明的档位；
- 至少保留一个候选，最后一个不能删；
- 候选表里**恰好一个**模型带 `frontier: true`：它代表"最强档"，决定 codex-dry 时哪一档
  走 GLM；代码里的 `ASTRA` 常量就等于这个模型，所以换前沿模型不需要改源码；
- `set-frontier MODEL` 移动这个标记；删除带标记的候选时 `remove` 会自动把标记挪给剩余
  的第一个候选，`validate` 也会拒绝"一个都没有"或"多个"的表；
- `--dry-run` 只看结果不写；`--no-test` / `--no-restart` 按需跳过。

替换“前沿档”候选时记得两件事：`set-frontier` 把标记挪给新模型，并用 `set-fallback`
把 fail-open 目标也挪过去——两者是独立的字段，`remove` 只会自动兜底，不会替你选新的
最强档。

验证：`scripts/verify_stack.py --live` 会打印这次真实请求路由到的模型；决策日志里看
`tier` / `model` / `effort`。不带 `--live` 时也会检查「Jev 候选可解析」：每个候选和
fallback 都必须能在目录里找到对应的 gateway，否则直接 FAIL。

## B. 加/删某个 provider 的模型

先看该 provider 有什么、当前选了什么：

```bash
python3 scripts/calibrate_models.py --providers aihubmix --discover
```

输出分两段：`Registered:`（已在 `user-models.json` 里）和 `New addable candidates:`（可加）。

`--discover` 只能列出 provider 目录里声明的 id。AIHubMix 会透传一些目录里**没有**的
id（例如 `gpt-6-astra`：`/api/v1/models` 查不到，端点却正常返回），这类模型
`--discover` 看不到，只能按下面第二条用 `--add` 显式登记，上下文窗口和档位自己给
（先按上游报错/模型规格探测，别猜）。

```bash
# 加一个（上下文窗口必填；autoCompact 默认取 0.85 倍）
python3 scripts/calibrate_models.py --providers aihubmix --add glm-5.2 \
  --ctx 1048576 --levels low,high,max --default max --apply

# 只有单档的模型
python3 scripts/calibrate_models.py --providers aihubmix --add grok-4.7 \
  --ctx 500000 --levels high --apply

# 删一个（同时把 picker 里的条目隐藏；--keep-picker 可保留）
python3 scripts/calibrate_models.py --providers aihubmix --remove glm-5.2 --apply
```

行为与约束：

- `--add` / `--remove` 一次只能针对一个 provider（要和 `--providers` 一起给，且只有一个值）；
- `--add` 会补全 identity 字段（`slug` / `gatewayModel` / `compHash`），已存在则按校准更新；
- `--default` 必须出现在 `--levels` 里，否则拒绝；
- 写前备份到 `<state-dir>/backups/<时间戳>-before-calibrate|before-remove/`；
- `--apply` 后自动 `refresh-catalog`，随后重启 Codex Router 让新模型立刻可路由：
  路由器在进程启动时建表，只写文件不重启的话它会继续用旧表；`--no-refresh` 跳过
  这两步，`--no-reload` 只跳过重启；
- 桌面端要**完全退出重开**才看到新目录（它是启动时读目录的）。

如果要维护一份固定清单（装机、换机、团队统一），改 `references/calibration.json` 这个
数据源，然后：

```bash
python3 scripts/calibrate_models.py --providers aihubmix,ccsub,jev --dry-run
python3 scripts/calibrate_models.py --providers aihubmix,ccsub,jev --apply
```

## 顺序建议

1. 先加下游：`calibrate_models.py --add`（并确认 `verify_stack.py` 目录项通过）；
2. 再加/换 Jev 候选：`manage_jev_tiers.sh add|replace`；
3. `verify_stack.py --live` 确认真实请求走通；
4. 最后删旧模型：`manage_jev_tiers.sh remove`，再 `calibrate_models.py --remove`。
