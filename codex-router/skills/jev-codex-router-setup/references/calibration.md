# 模型校准表

数据源：`references/calibration.json`（唯一可执行来源）。
改数值只改 JSON，然后用 `scripts/calibrate_models.py --dry-run` 看差异，
再 `--apply`，再 `refresh-catalog`，最后重启 Codex 客户端。

## 校准规则

- `defaultEffort` 必须出现在该模型的 `reasoningLevels` 里，否则注册表校验会拒绝。
- `autoCompact` 必须小于 `contextWindow`；写小一点给工具结果留余量。
- provider 目录**明确声明**了思考档位时才写多档；没有声明就只保留单档 `high`
  （“兼容档”），不要凭猜测编档位。
- 已有条目的展示名/描述/优先级不动，脚本只覆盖校准字段。

## 当前表（2026-09-23）

| 模型 | contextWindow | autoCompact | 档位 | 默认 |
|---|---:|---:|---|---|
| `jev/auto` | 258400 | 219640 | low/medium/high/xhigh/max | medium |
| `aihubmix/deepseek-v4.1-flash` | 1000000 | 850000 | low/high/max | high |
| `aihubmix/glm-5.3` | 1048576 | 900000 | low/high/max | max |
| `aihubmix/glm-5.3-flash` | 1048576 | 400000 | low/high/max | max |
| `aihubmix/kimi-k3` | 1048576 | 900000 | low/high/max | max |
| `aihubmix/qwen-3.8-27b` | 131072 | 110000 | high | high |
| `aihubmix/grok-4.7` | 500000 | 425000 | high | high |
| `aihubmix/mimo-v2.6-flash` | 1048576 | 900000 | high | high |
| `aihubmix/mimo-v2.6-pro` | 1048576 | 900000 | high | high |
| `aihubmix/gpt-6-astra` | 272000 | 231200 | low/medium/high/xhigh/max | medium |
| `ccsub/*`（10 个） | 375000 | 318750 | high | high |

## 证据与口径

**AIHubMix（`https://aihubmix.com/api/v1/models?type=llm`）**，2026-09-22 拉取：

- `deepseek-v4.1-flash` 的 `reasoning_options` 为
  `[{type: toggle}, {type: effort, values: [low, high, max], default: high}]`；
  上游 `model` 字段回显 `deepseek-flash`。所以是低/高/最高三档、默认高。
- `glm-5.3`、`glm-5.3-flash`、`kimi-k3` 同样声明 `low/high/max`。
- `qwen-3.8-27b`、`grok-4.7`、`mimo-v2.6-*` 未声明 effort 列表 → 保留单档 `high`。
- 上下文窗口按 provider 目录给的数值，`autoCompact` 取窗口的约 85%（flash 类按实测
  消息量下调，例如 `glm-5.3-flash` 取 400000）。

**`aihubmix/gpt-6-astra`（2026-09-23 实测）**：AIHubMix 的公开模型列表（410 条）
里**没有**这个 id，但端点直接服务它——请求返回 200、`model` 回显 `gpt-6-astra`、
正文正常，属于未文档化的透传 id，所以不能靠 `--discover` 发现，只能用
`calibrate_models.py --add` 显式登记。

- 档位来自端点自己的校验信息：`reasoning.effort` 传 `ultra` 或乱填会被 400 拒绝，
  报错里列出支持值 `none, minimal, low, medium, high, xhigh, max`。据此写
  `low/medium/high/xhigh/max`（`ultra` 不在内），默认取 `medium`，与 `ccsub/gpt-6-*`
  和 `jev/auto` 的默认档一致。
- 上下文窗口目录未声明，按该模型自身声明的 272000 取，`autoCompact` 取 85%
  （231200）。上游是否真的吃满 272k 未做实测。
- 这个 id 现在是 Jev 候选池里的 `astra` 档；CCSub 同名 id（`ccsub/gpt-6-astra`）
  仍然上游 503，两条链路不是一回事。

**CCSub**：provider 目录（`https://ccsub.inferera.com/v1/models`）只声明 id 和
displayName，不给上下文窗口，也不给思考档位。2026-09-22 按 X 上“Codex 客户端默认
375k 上下文”的口径，把 10 个条目的 `contextWindow` 从保守值 131072 提到 375000、
`autoCompact` 取 318750（0.85）。档位仍是单档 `high`：目录没有声明阶梯，不要凭
猜测编多档。上游是否真的接受 375k 未做实测，超出真实上限时会直接 400。

2026-09-23 复查：`ccsub/gpt-6-astra` 请求上游返回 503
（`gpt-6-astra (curated) is unavailable right now`），`ccsub` 的 `discovered`
列表里也没有 astra；同一家的 `gpt-reserve` 是新出现的 id，没有元数据、未验证。
要接 astra 走 AIHubMix 那条（见上）。

**`jev/auto`**：上下文与 autoCompact 跟随路由策略里的声明；档位是 Codex 的通用阶梯，
由 `server/routing_policy.py` 映射到具体执行模型。

## 与客户端可见性的关系

校准表里写了档位，不等于客户端一定显示：Codex 客户端会再与本地设置
`enabled-reasoning-efforts` 求交集，该默认值不含 `max`。这就是“声明了三档、
界面只有两档”的原因，处理方式见 [troubleshooting.md](troubleshooting.md)。
