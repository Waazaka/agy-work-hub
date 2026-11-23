---
name: hub
description: >-
  Pure-local multi-project human-AI collaboration orchestrator (/hub) with a
  context firewall, external memory cards, configurable G0-G3 model routing via
  models.json, PreToolUse guardrails, and five-layer adversarial verification.
---

# Hub 纯本地四档变速编排与独立测试协作规程 (`/hub`)

本技能指导 **Hub 总控（Coordinator）** 在纯本地环境管辖工作区内的多项目协作。完整宪章见 [`AGENTS.md`](../../../../../AGENTS.md)，动态模型配置文件见 [`models.json`](../../models.json)，模型配置工具见 [`configure_models.py`](../../scripts/configure_models.py)。

---

## 一、 总控按需轻快预检

1. **按需读卡与施工挂牌（防失忆与防撞车）**：
   - **单项目任务**：直接 `read_file` 拉取目标项目 `boards/<slug>.md`（老窗口跨轮必重读，免开场通读全套文档）；仅当**全局盘点进度或目标项目尚未建卡**时，才读取 [`DASHBOARD.md`](../../../../../DASHBOARD.md)（未建卡先派只读 `research` 探针按 [`_TEMPLATE.md`](../../../../../boards/_TEMPLATE.md) 建卡）。
   - 检查 `Doing` 栏是否存在 `🚧 [施工中]` 占用提示；G1/G2/G3 派单开工前先在 `Doing` 栏挂 `🚧 [施工中 · MM-DD HH:MM]` 挂牌，完工落盘后清零。
2. **查人机门禁（Gate 2 · 纯本地 + `search_web`）**：
   - 对照 [`workflow-gates.md`](../../../../../playbook/workflow-gates.md)，凡涉及新核心能力（解析/检索/转换/不确定交互）且未选型，先派本地只读 `research` 子代理查项目现有依赖（`package.json` / `pyproject.toml` 等），并调用内置 **`search_web`** 联网扫描成熟开源备选与 2～3 款同类标杆产品交互，汇总简表交人类拍板后再编码。
3. **尊重人类手写现场**：任何下层 Agent 进场第一步必查目标项目 `git status` / `git diff --stat`，顺着人类未提交改动接力，严禁覆盖。

---

## 二、 G0～G3 四档智能变速箱与 `models.json` 动态绑定

> **模型绑定唯一真相源**：拉起任何 Swarm 或配置子代理前，必须先读取 [`models.json`](../../models.json)（或执行 `python3 <plugin>/scripts/configure_models.py --show --json`）：
> - 读取 `roles` 映射的 8 个角色键：`coordinator`、`planner`、`impl_g1`、`impl_g2_g3`、`verifier_g1`、`verifier_g2_g3`、`monitor`、`research`。
> - 若某角色配置为 **`"inherit"`**，调用 `swarm{action: "add", ...}` 时**省略 `model` 参数**（`invoke_subagent` 传 `Model: "inherit"`），继承当前主会话模型。
> - 若某角色配置为精确模型 ID，则在 `swarm{action: "add", ...}` 中精确传入 `model: "<配置值>"`，且严禁使用命中 `blocked_models` 的模型。
> - 若人类口头提出调整模型（例如「把 G2 的 planner 换成 xxx」「切到省流档 / inherit-all」「禁用某模型」），总控直接调用 [`configure_models.py`](../../scripts/configure_models.py) 完成修改并汇报当前配置。

```bash
# 查看当前模型路由表 / 列出 4 套内置预设（balanced / economy / max-quality / inherit-all）
python3 .agents/plugins/work-hub/scripts/configure_models.py --show
python3 .agents/plugins/work-hub/scripts/configure_models.py --list-presets

# 一键切换预设 / 单项修改某角色模型 / 校验配置合法性
python3 .agents/plugins/work-hub/scripts/configure_models.py --preset economy
python3 .agents/plugins/work-hub/scripts/configure_models.py --set planner=inherit --set impl_g2_g3=custom-model
python3 .agents/plugins/work-hub/scripts/configure_models.py --validate
```

### 0. G0 档 · 闪答 / 直改档（不建 Swarm）
- **准入硬判据**：不涉及多文件业务源码改造与本地回归（如进度盘点、改看板、日常问答、模型配置调整），或人类当轮明确指令「直接改 / 不用走总控 / edit directly / bypass hub」的单文件微调。
- **动作**：
  - **问答 / 看板 / 明示单文件直改**：由总控主窗口秒回处理；
  - **纯本地只读盘点**：派 `invoke_subagent(TypeName: "research", Model: "<roles.research>")`（默认 `"inherit"`），返回 ≤10 行摘要即焚；
  - **外部开源 / 竞品调研**：调用内置 `search_web` 完成检索与对比。

### 1. G1 档 · 快刀开发档（轻快双独立会话 Swarm）
- **准入硬判据（需同时满足）**：预计改动 **≤2 个业务文件**、边界清晰、不碰数据库 Schema/迁移或受保护契约、无需 Gate 2 新选型；或人类口令「走快刀档」。
- **Swarm 编排**（跳过 `@planner`，快审快交；按 `models.json` 传入对应角色模型，`"inherit"` 时省略 `model`）：
  - **独立编码员（`impl`）**：`swarm{action: "add", name: "impl", [model: roles.impl_g1], brief: "[HUB_ROLE:IMPL] ..."}`（见 [`references/role-impl.md`](./references/role-impl.md)）
  - **独立验收员（`verifier` —— 严禁与 `impl` 共用会话！）**：`swarm{action: "add", name: "verifier", [model: roles.verifier_g1], brief: "[HUB_ROLE:VERIFIER] ..."}`（见 [`references/role-verifier.md`](./references/role-verifier.md)）
  - **巡检守卫（`monitor`）**：按 `swarm_monitor` 规范挂载，绑定 `roles.monitor`。
- **闭环与自动升档**：`impl ↔ verifier` 本地独立盲测/锁测试闭环；若发现波及 ≥3 文件/需新选型或 **G1 被打回 ≥2 次**，立即升档至 G2。

### 2. G2 档 · 主审攻坚档（本地主审规划 → 独立实现 → 五层对抗盲测）
- **准入硬判据（命中任一即入档）**：**跨 ≥3 个业务文件 / 多模块**、**触及数据库 Schema / 迁移 / 受保护契约**、**需 Gate 2 开源选型**、**根因未明疑难 Bug 或 G1 被打回 ≥2 次**；或人类口令「走重装档 / 叫军师先审一下」。
- **Swarm 编排与纯本地闭环**：
  - **主审核查官（`planner`）**：`swarm{action: "add", name: "planner", [model: roles.planner], brief: "[HUB_ROLE:PLANNER] ..."}`（见 [`references/role-planner.md`](./references/role-planner.md)）。只读核查现场 `git status`/依赖/源码，输出**《施工蓝图 + 架构禁区 + 对抗性 DoD》**；若命中 Gate 2 选型或有必须人类裁决的分歧则 `@coordinator` 上报；**若边界清晰无分歧，直接在看板 `@impl` 下达蓝图点火开工**。
  - **独立实现员（`impl`）**：`swarm{action: "add", name: "impl", [model: roles.impl_g2_g3], brief: "[HUB_ROLE:IMPL] ..."}`（先 `swarm_wait` 待命接 `@planner` 蓝图，编码接线与自测后在看板 `@verifier` 提测；修 Bug 流带 `[LOCK_TESTS]` 禁改测试断言）。
  - **独立验收员（`verifier` —— 严禁与 `impl` 共用会话！）**：`swarm{action: "add", name: "verifier", [model: roles.verifier_g2_g3], brief: "[HUB_ROLE:VERIFIER] ..."}`（新功能执行五层对抗盲测并导出 `test_output.log`；修 Bug 先带 `[ALLOW_RED_TEST]` 写失败红测锁定缺陷）。
  - **巡检守卫（`monitor`）**：按 `swarm_monitor` 规范挂载，绑定 `roles.monitor`。
- **清场回收与防漂移**：验收 `PASS` 后将结论更新至 `boards/<slug>.md`（≤180 行）并清零 `Doing` 栏 `🚧 [施工中]` 挂牌，随后 `kill` 释放全部临时 Swarm 成员；同一总控窗口连跑 2~3 个大阶段后提醒人类开新窗或 `/compact`。

### 3. G3 档 · 并行战役档（多副本独立并行 + 集成五层盲测）
- **准入硬判据**：单项目内 **≥3 个解耦模块并行开发**与交叉联调。
- **编排规范**：`@planner`（`roles.planner`）划界切分各模块文件归属权（零交叉写同文件）并在看板直接点火多路 `@author-*`（均按 `roles.impl_g2_g3` + `workspace: "share"` 独立工作副本）+ 独立 `@verifier`（`roles.verifier_g2_g3`）做合并后五层集成盲测 + `@monitor`（`roles.monitor`）；详见 [`references/swarm-playbook.md`](./references/swarm-playbook.md)。

---

## 三、 总控向人类交付的四段式标准

1. **交付结论**（一句话说明完成了什么）；
2. **开发与独立测试实证**（列出改动文件清单，以及独立 `@verifier` 实跑的完整命令、退出码、`pass / fail / skip` 统计与坏输入/真实入口验证）；
3. **👉 你的真实路径体验指引（3 步以内）**；
4. **经验沉淀 / 下一步建议**（是否提炼至 `judgment-rules.md` / 更新 `boards/<slug>.md`）。
