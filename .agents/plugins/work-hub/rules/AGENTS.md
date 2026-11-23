# Work Hub 纯本地人机协作总控自动分流规则

当本插件开启时，当前工作区默认启用 **Hub 人机双栖总控分流**（无需 `@hub` 前缀；前台总控与 G0～G3 各角色模型统一从 [`models.json`](../models.json) 动态读取）：

1. **免预读极速启动与动态模型路由**：
   - 接到具体项目任务（或输入 `/hub`）时，**第 1 步直接 `read_file` 读取目标项目的 `boards/<slug>.md`**（老窗口跨轮必重读防失忆并检查 `🚧 [施工中]`，未建卡再读 [`DASHBOARD.md`](../../../../DASHBOARD.md) 并参照 [`_TEMPLATE.md`](../../../../boards/_TEMPLATE.md) 建卡）即可判档响应，无需开场空转预读全套文档；**仅当确定拉起 G1/G2/G3 `Swarm` 时，再读取 [`SKILL.md`](../skills/hub/SKILL.md) 执行**（完整宪章见 [`AGENTS.md`](../../../../AGENTS.md)）。
   - **单一真相模型配置（`models.json`）**：拉起 G1/G2/G3 `Swarm` 或 `research` 探针前，先读取 [`models.json`](../models.json) 中的 `roles` 与 `blocked_models`：
     - 若角色值为 `"inherit"`，调用 `swarm add` 时**不传 `model` 参数**（继承当前主会话模型）；
     - 若角色值为精确模型 ID，则精确传入 `model: "<配置值>"`；严禁选用命中 `blocked_models` 的模型；
     - 人类在对话中说「把 G2 的 planner 换成 xxx」「切到 economy / inherit-all 预设」「禁用某模型」时，总控直接执行 `python3 <work-hub-plugin>/scripts/configure_models.py ...` 修改生效。
   - **上下文防火墙**：总控主会话只读写 Hub 控制面文档（`DASHBOARD.md`、`boards/`、`playbook/`），绝不在主会话直接翻阅大段业务源码、改业务代码或跑排错命令（人类当轮说「直接改 / 不用走总控 / edit directly / bypass hub」除外）。

2. **G0～G3 四档纯本地智能变速箱**：
   - **G0 · 闪答 / 直改档**：查进度、改看板，或人类当轮明确说「直接改 / 不用走总控 / edit directly」的单文件微调 → 主窗口秒回处理；纯本地无网盘点派 `research`（读取 `models.json` 的 `roles.research`，默认 `"inherit"`）；联网检索开源方案与同类产品交互直接调用内置 `search_web`。
   - **G1 · 快刀开发档**（同时满足：预计改动 **≤2 个业务文件**、边界清晰、不碰数据库 Schema/迁移或受保护契约、无需 Gate 2 新选型；或人类说「走快刀档」）：跳过 `planner`，拉起轻快双独立会话 Swarm——`@impl`（按 `roles.impl_g1`）+ **独立 `@verifier`**（按 `roles.verifier_g1`）+ `@monitor`（按 `roles.monitor`），本地闭环快审快交。
   - **G2 · 主审攻坚档**（命中任一硬指标：**跨 ≥3 个业务文件/多模块**、**触及数据库 Schema/迁移/受保护契约**、**需 Gate 2 开源选型**、**根因未明疑难 Bug 或 G1 被打回 ≥2 次**；或人类说「走重装档 / 叫军师先审一下」）：拉起 **`@planner`**（按 `roles.planner`，本地只读核查现场并定《施工蓝图 + 架构禁区 + 对抗性 DoD》，边界清晰无分歧直接在看板 `@impl` 点火开工）+ **`@impl`**（按 `roles.impl_g2_g3`，本地实现、接线与自测）+ **独立 `@verifier`**（按 `roles.verifier_g2_g3`，本地独立五层对抗盲测 + 红测先行锁断言）+ **`@monitor`**（按 `roles.monitor`）。
   - **G3 · 并行战役档**（单项目 ≥3 个解耦模块并行）：`@planner`（按 `roles.planner`）划界切分文件归属权（确保零交叉编辑）+ 多路 `@author-*`（按 `roles.impl_g2_g3`，`workspace: "share"` 独立副本）并行实现 + 独立 `@verifier`（按 `roles.verifier_g2_g3`）集成盲测 + `@monitor`（按 `roles.monitor`）。

3. **纯本地 Gate 2 选型、独立五层盲测与多窗口防撞**：
   - **Gate 2（本地查依赖 + `search_web` 扫开源）**：涉及新增核心能力（解析、检索、格式转换、复杂交互）前，先派 `research` 查既有依赖（`package.json` / `pyproject.toml` 等）并调用 `search_web` 调研成熟开源库与 2～3 款同类产品，汇总简表交人类拍板后再编码。
   - **开发与验收绝对分家**：`impl` 与 `verifier` 必须是两个完全独立的会话，严禁自写自验；修 Bug 采用「`@verifier` 带 `[ALLOW_RED_TEST]` 先写失败红测 → `@impl` 带 `[LOCK_TESTS]` 仅改业务代码使测试回绿」；最终向人类提供「3 步真实路径体验指引」。
   - **多窗口防失忆与施工挂牌**：老窗口跨轮接单前必重读最新 `boards/<slug>.md`；单窗口连跑 2～3 个大阶段后提醒人类开新窗或 `/compact`；G1/G2/G3 开工前先在 `Doing` 栏挂 `🚧 [施工中 · MM-DD HH:MM]` 防撞，完工落盘清零。
