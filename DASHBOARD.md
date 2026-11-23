# Work 全局人机协作看板 (`DASHBOARD.md`)

> **纯本地多项目外置大脑**：记录全局活跃项目索引、模型挡位摘要与人类灵感收件箱。详细总控宪章见 [`AGENTS.md`](./AGENTS.md)。

---

## ⚙️ 当前模型路由与协作铁律

- **模型路由单一真相**：[`models.json`](./.agents/plugins/work-hub/models.json)（内置 4 套预设：`balanced` / `economy` / `max-quality` / `inherit-all`，支持通过 [`configure_models.py`](./.agents/plugins/work-hub/scripts/configure_models.py) 按角色自由切换或设为 `inherit`）。
- **G0～G3 四档纯本地分流**：
  - **G0 · 闪答 / 直改**：进度查询、看板更新、人类明示「直接改 / 不用走总控 / edit directly」的单文件微调，或纯本地 `research` / `search_web` 调研（0 Swarm 开销）。
  - **G1 · 快刀开发（≤2 业务文件、无数据库/受保护契约/新选型）**：跳过 `@planner`，`@impl` + 独立 `@verifier` + `@monitor` 快审快交。
  - **G2 · 主审攻坚（≥3 业务文件/多模块、Schema 迁移、Gate 2 选型、疑难 Bug 或 G1 打回 ≥2 次）**：`@planner`（只读现场定《施工蓝图 + DoD》直连点火）+ `@impl` + 独立 `@verifier`（五层对抗盲测 + 红测锁断言）+ `@monitor`。
  - **G3 · 并行战役（≥3 解耦模块并行）**：`@planner` 划界 + 多路 `workspace: "share"` `@author-*` 并行实现 + 独立 `@verifier` 集成盲测 + `@monitor`。
- **硬件级护栏**：[`hub_guard.py`](./.agents/plugins/work-hub/scripts/hub_guard.py) 自动保护共享文档免于整篇覆盖、管控记忆卡 ≤220 行、拦截高危 Git 命令、强制角色隔离与 `[LOCK_TESTS]` 测试断言锁死。

---

## 📥 灵感与待办收件箱 (Human Inbox)

*（平时想到点子、待办或规则调整直接记在这里；对总控说「处理 Inbox」即可分拣执行）*

- [ ] 将当前工作区的实际项目按 [`boards/_TEMPLATE.md`](./boards/_TEMPLATE.md) 登记到下方项目索引表
- [ ] 运行 `python3 .agents/plugins/work-hub/scripts/configure_models.py --show` 核对适合当前账号的模型预设

---

## 一、 活跃项目总览 (Project Directory)

| 代号 (`slug`) | 项目名称与定位 | 相对/工作区路径 | 记忆卡 | 当前状态 |
| :--- | :--- | :--- | :--- | :--- |
| `example-app` | **Example App（示例项目）**：全栈任务管理演示应用（React + FastAPI / SQLite） | `<your-workspace>/example-app` | [example-app.md](./boards/example-app.md) | 🟢 **示例就绪**（模板记忆卡，接入自有项目后可替换或归档） |

---

## 二、 接入新项目指引

1. 从 [`boards/_TEMPLATE.md`](./boards/_TEMPLATE.md) 复制一份记忆卡到 `boards/<your-slug>.md`；
2. 在上方表格追加一行登记（保持项目代号、相对路径与记忆卡链接清晰）；
3. 之后直接在对话中提及该项目名称，总控会自动先读取对应 `boards/<your-slug>.md` 判档调度。
