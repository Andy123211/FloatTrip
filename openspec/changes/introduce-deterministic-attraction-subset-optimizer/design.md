## Context

现有规划图让 LLM 直接输出完整路线，再由 Reviewer 和 Time Checker 反复修订。确定性代码只在手动 API 中枚举一天内的景点排列，不能决定景点子集、跨天分配或时刻表。候选池最多30个、每天最多5个，适合用有时间窗和可选节点的组合优化模型处理。

本变更需要同时修改 LLM 契约、POI 数据、规划状态、求解算法、最终结果和餐厅流程，并引入 OR-Tools 依赖。工作区正在进行主 Agent 改造，因此新实现保持 Planning/Revision Run 边界，Revision 首期继续使用旧图。

## Goals / Non-Goals

**Goals:**

- LLM 只筛选并标注较宽候选池，确定性代码选择子集、分天、排序和排时。
- 硬约束与软评分分离，评分由不可变版本配置控制并可独立重算。
- 规则优先识别小吃街、夜市等用餐场景景点，并将其安排到合适餐次窗口。
- 求解失败时只放宽系统最小数量，永不放宽用户硬约束。
- 餐厅推荐不参与核心路线，并尊重景点产生的用餐覆盖。

**Non-Goals:**

- 不接入真实道路交通矩阵；相邻景点仍使用20分钟固定换乘缓冲。
- 不在首期迁移 Revision Graph。
- 不把 LLM confidence、理由或任意数字作为评分权重。
- 不承诺在1秒时限内证明全局最优。

## Decisions

### 候选池而非完整路线

新增 `CandidatePoolProposal`。模型从权威 POI 中返回12–24个 `CandidateAttraction`，只提供停留时长和受控枚举语义。服务端重新关联高德坐标、评分和开放时间，强制加入必去点并移除避开点。相比让模型返回少量完整路线，该方案让代码拥有足够的组合决策空间。

### CP-SAT 可选节点多日路径模型

每个 POI 建立选择、分天、开始时间变量；每一天使用带 self-loop 的可选节点 Circuit 表达访问顺序。弧约束加入景点停留时长和固定20分钟缓冲。选择收益与路线惩罚共同进入整数化目标函数。

选择 CP-SAT 而不是自研 Beam Search，是为了让硬约束可声明、可验证并获得 Best Bound。求解使用单线程、固定种子和1秒墙钟上限。`FEASIBLE` 结果必须携带 Gap，只有 `OPTIMAL` 才声称数学最优。

### 用餐场景是景点语义

高德 `type/typecode` 不再丢弃。规则先识别小吃街、美食街、夜市和午市；LLM 只能在规则未知时从 `none/lunch/dinner/either` 补充。午餐窗口为11:30–13:30，晚餐窗口为17:30–20:00，至少重叠45分钟才形成 coverage；`either` 一次游览最多覆盖一餐。

### 不可变版本化评分

`balanced-v1` 配置定义所有收益和惩罚系数。注册表中的每个组件同时提供 Solver 表达式和纯代码 evaluator，Quality Gate 用 evaluator 重算 Objective。Run snapshot 和 itinerary 都保存配置版本及候选池 fingerprint；已使用版本禁止原地修改。

### 分层降级

严格模型无解时，仅把系统生成的每日最小数量降为1并再次求解。仍无解则使用确定性聚类贪心和日内全排列；再失败才触发一次 Candidate Repair。任何路径都必须通过同一个 Quality Gate。

### 餐厅独立 enrichment

核心 timeline 只包含 attraction。按需 Restaurant Task 从已保存路线读取用餐覆盖；覆盖餐次不搜索外部餐厅，只可提供街区吃法建议。餐厅结果按 route fingerprint 独立保存，不修改核心 `plan_json`。

### DeepSeek Think 两阶段结构化输出

DeepSeek Think 支持自主工具调用，但拒绝结构化输出封装所需的指定函数
`tool_choice`。所有显式启用 Think 的结构化规划调用因此拆成两阶段：第一阶段使用
Think 且不绑定工具，依据目标 JSON Schema 产出完整语义草稿；第二阶段使用同一模型的
非 Think 模式，通过 Strict Function Calling 将草稿转换并校验为既有 Pydantic Schema。
第二阶段只负责格式化，不重新执行旅游决策。未启用 Think 的现有结构化调用维持单阶段。

## Risks / Trade-offs

- [1秒上限可能只得到局部可行解] → 保存 Bound/Gap，并提供确定性兜底与离线 Shadow 评测。
- [LLM 语义标注漂移] → 使用枚举 Schema、权威 POI 回连、规则优先和独立审计字段。
- [CP-SAT Objective 与展示评分不一致] → 同一评分注册表提供表达式和 evaluator，Gate 必须重算一致。
- [餐次窗口和固定缓冲不等于真实交通] → 在结果中标记为规划估算，后续可用真实交通提供器替换。
- [旧前端依赖 lunch/dinner timeline] → 输出层保持字段向后兼容但允许缺失，先补回归测试再切换默认图。
- [两阶段调用增加延迟与 token 成本] → 仅对显式启用 Think 的结构化调用启用，并让第二阶段只接收语义草稿。
- [Think 草稿为空或严格格式化失败] → 作为结构化调用失败交给既有有限重试和 Run 失败语义处理，不提交半成品。

## Migration Plan

1. 增加 POI 字段、候选 Schema、评分配置、求解器及独立测试，不切主图。
2. 在 feature flag 下接入候选规划和 Quality Gate，保留 legacy 图回滚。
3. 调整 finalize 和前端以接受纯 attraction timeline。
4. 增加 restaurant enrichment 持久化与按需任务。
5. 用冻结 fixture 比较 legacy、active 和 shadow 指标后切换默认值。

回滚通过 `PLANNING_PIPELINE_MODE=legacy` 完成；新字段和 enrichment 表向后兼容，无需删除数据。

## Open Questions

- 真实运行数据积累后再决定是否把1秒上限提升为确定性时间预算或使用并行求解。
- Shadow Profile 的自动晋升阈值留待形成稳定离线评测集后定义。
