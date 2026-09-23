# agents-router

把「让 agent 自己挑模型」这套能力做成可复用的集成：一份可安装的 skill，加上它需要的
源码快照。目标是在一台新机器上，把外部模型接进 Codex，并让 `jev/auto` 按每一轮的难度
自动选择模型和思考档位。

目前包含一个集成：

| 目录 | 集成 | 说明 |
|---|---|---|
| [`codex-router/`](codex-router/) | Codex + Jev 自动路由 | 通过 Codex Router 注册外部 provider，`jev/auto` 在候选池里自动路由 |

## 快速开始

```bash
# 1. 安装 skill（把目录放到 Codex 的 skills 目录）
cp -R codex-router/skills/jev-codex-router-setup ~/.codex/skills/

# 2. 按 skill 的流程走；第一步就是准备源码
bash ~/.codex/skills/jev-codex-router-setup/scripts/bootstrap_jev_repo.sh \
  --clone ~/AIProjects/agent-labs/jev-codex-router
```

完整步骤、平台差异和排错见
[`codex-router/skills/jev-codex-router-setup/SKILL.md`](codex-router/skills/jev-codex-router-setup/SKILL.md)。

## 安全

所有密钥都只在**你自己的终端**里通过隐藏输入录入，不进对话、不进命令行参数、不进日志。
仓库里没有任何凭据；脚本只打印「已配置/跳过」。

## 许可

[MIT](LICENSE)，© 2026 qiaoqiufei。

`codex-router/src/jev-codex-router/` 是上游
[0xNatoshi/jev-codex-router](https://github.com/0xNatoshi/jev-codex-router) 的快照，
同样是 MIT（© 2026 Thibault Saint-Jean）；它自己的 `LICENSE` 随目录保留，再分发请一并带上。
