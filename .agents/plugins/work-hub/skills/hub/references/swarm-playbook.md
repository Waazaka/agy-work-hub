# Swarm 并行突击队启动手册 (`swarm-playbook.md`)

> **与 [`SKILL.md`](../SKILL.md) 及 [`models.json`](../../../models.json) 保持单一真相**：
> - **拉队前必读配置**：每次启动 G1/G2/G3 Swarm 前，先读取 [`models.json`](../../../models.json)（或运行 `python3 <plugin>/scripts/configure_models.py --show --json`）获取当前生效的 `roles` 与 `blocked_models`：
>   - 若角色值为 **`"inherit"`**，调用 `swarm{action: "add", ...}` 时**省略 `model` 参数**（继承当前主会话模型）；
>   - 若角色值为精确模型 ID，则精确传入 `model: "<配置值>"`；严禁选用命中 `blocked_models` 的模型。
> - **G0 · 闪答 / 直改档（不建 Swarm）**：查进度、改看板或人类明示「直接改 / 不用走总控 / edit directly」的单文件微调，由总控主窗口处理；纯本地无网盘点派 `invoke_subagent(TypeName: "research", Model: roles.research)`；外网开源/竞品检索调用内置 `search_web`。
> - **G1 · 快刀开发档（轻快双独立会话 Swarm）**：同时满足 **≤2 个业务文件**、边界清晰、不碰数据库/受保护契约、无需 Gate 2 新选型时，跳过 `@planner`，启用 `@impl`（`roles.impl_g1`）+ 独立 `@verifier`（`roles.verifier_g1`）+ `@monitor`（`roles.monitor`）。
> - **G2 · 主审攻坚档（本地主审规划 → 独立实现 → 五层对抗盲测）**：命中 ≥3 业务文件/多模块、数据库 Schema/迁移/受保护契约、Gate 2 选型、疑难 Bug 或 G1 打回 ≥2 次时，启用 `@planner`（`roles.planner`）定蓝图 + `@impl`（`roles.impl_g2_g3`）实现与自测 + 独立 `@verifier`（`roles.verifier_g2_g3`）五层盲测 + `@monitor`（`roles.monitor`）。
> - **G3 · 并行战役档（多副本独立并行 + 集成盲测）**：单项目内拆分为 ≥3 个解耦模块并行开发时，启用 `@planner`（`roles.planner`）划界切分文件归属权 + 多路 `workspace: "share"` 独立副本 `@author-*`（`roles.impl_g2_g3`）+ 独立 `@verifier`（`roles.verifier_g2_g3`）+ `@monitor`（`roles.monitor`）。

## 标准启动与收尾流程

1. **战前划界（防撞车第一铁律）**
   - G2/G3 由 `@planner`（`[HUB_ROLE:PLANNER]`）先审视现场与依赖、按模块或文件目录切分归属权并输出《施工蓝图 + 架构禁区 + 对抗性 DoD》（确保绝无两个 `author` 同时改同一个文件）。
   - 为每个并行改代码的 `author` 设置 `workspace: "share"`（各自拥有独立工作副本）；只读的 `planner`、`verifier`、`monitor` 保持默认 `inherit`。
2. **精简建队**
   - 命名清晰：`swarm{action:"title", title:"<项目名>: <战役目标>"}`。
   - 严格按 [`models.json`](../../../models.json) 对应角色绑定模型（`"inherit"` 时不传 `model`）；若当前技能列表含 `swarm_monitor`，挂载 `@monitor` 时绑定 `roles.monitor`。
   - 向人类展示 Swarm 看板入口，方便人类随时在侧栏查看进度或插话指导。
3. **看板点对点闭环（免二传手直连）**
   - 在每个成员的 `brief` 中明确点对点交接链路：
     - G2/G3 `@planner` 仅在命中 Gate 2 选型或必须人类拍板的分歧时才 `@coordinator` 上报；边界清晰无分歧时**直接在看板 `@impl` / `@author-*` 下达蓝图点火开工**；
     - `impl` / `author` 完成后直接在看板 `@verifier` 附上改动清单与自测命令（**不要抄送 `@coordinator`**）；
     - `@verifier` 发现问题直接 `@对应impl/author` 打回重修；只有全部五层盲测 `PASS` 后，才 `@coordinator` 汇报完整实证。
4. **战后落盘与解散**
   - 若使用了 `workspace: "share"`，将各通过验收的 `share` 副本改动合并回项目工作区，并运行一次整体确定性回归确认无冲突。
   - 将本次战役的关键架构变更与结论提炼写入 `boards/<slug>.md`（≤180 行）及 [`DASHBOARD.md`](../../../../../../DASHBOARD.md)，并清零 `Doing` 栏 `🚧 [施工中]` 挂牌。
   - 调用 `swarm{action:"kill", ...}` 清理已完成使命的临时成员，释放资源，向人类呈交最终交付与「3 步真实路径体验指引」。
