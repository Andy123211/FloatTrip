## Why

当前 Planner 直接产出完整逐日路线，确定性代码只能在极小的日内排列空间中优化距离，无法可靠决定“选哪些景点、分到哪一天、何时游览”。同时，小吃街、夜市等兼具景点与用餐场景的 POI 没有结构化语义，容易被安排到不合适时段或与后续餐厅推荐重复。

## What Changes

- 将 Planner 输出改为较宽的候选景点集合及受控语义，不再让 LLM 决定最终子集和路线。
- 新增 OR-Tools CP-SAT 确定性优化器，联合选择景点、跨天分配、日内顺序和游览时间。
- 引入版本化评分配置、可扩展软约束注册表、独立评分重算和 Solver 质量报告。
- 保留高德 `type/typecode`，规则优先识别小吃街、美食街、夜市等用餐场景景点，并在午餐/晚餐窗口给予确定性收益。
- 新增确定性降级：系统每日最小数量放宽、地理聚类贪心兜底和一次 Candidate Repair。
- 将餐厅推荐从核心规划中剥离；已由用餐场景景点覆盖的餐次不再推荐外部餐厅。
- **BREAKING**：新生成 itinerary 的核心 `timeline` 仅由景点组成，不再自动插入 `lunch/dinner` 条目。

## Capabilities

### New Capabilities

- `deterministic-attraction-optimization`: 候选景点语义、子集选择、跨天分配、时刻表求解、版本化评分与质量校验。
- `restaurant-enrichment`: 按需餐厅推荐、用餐场景覆盖以及与核心 itinerary 解耦的 enrichment 语义。

### Modified Capabilities

- `structured-planning-constraints`: 规划快照需要冻结可由确定性优化器消费的硬约束和评分版本。
- `agent-run-lifecycle`: 核心规划失败不得保存不合法 itinerary，独立餐厅任务具有可重试的 Run 生命周期。

## Impact

- 影响规划 Schema、Prompt、LangGraph 节点、POI 解析、最终 itinerary 投影和手动路线优化接口。
- 新增 `ortools` 运行依赖及版本化评分配置文件。
- 新增 restaurant enrichment 持久化和按需执行入口。
- 需要兼容现有 Revision Graph、旧 itinerary 和前端缺少 `lunch/dinner` 条目的情况。
