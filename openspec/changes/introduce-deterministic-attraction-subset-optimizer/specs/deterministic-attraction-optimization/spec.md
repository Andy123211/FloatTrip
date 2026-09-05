## ADDED Requirements

### Requirement: LLM 生成受控候选景点集合
系统 SHALL 让 LLM 从权威 POI 池生成较宽候选集合及受控旅游语义，而不是直接决定最终路线，并 MUST 由服务端验证每个候选名称和枚举字段。

#### Scenario: 生成候选集合
- **WHEN** 正式规划拥有目的地、日期和候选 POI
- **THEN** LLM 返回受配置上限约束的候选景点、停留时长和语义属性，且不指定最终跨天路线

#### Scenario: 必去景点被模型遗漏
- **WHEN** 用户硬约束要求必去某景点但 LLM 候选集合未包含它
- **THEN** 服务端从权威 POI 池强制加入该景点且不得受普通候选上限移除

### Requirement: POI 保留用餐场景语义
系统 SHALL 保留高德 `type/typecode`，并 MUST 规则优先地将小吃街、夜市等景点归一化为 `none/lunch/dinner/either` 用餐场景。

#### Scenario: 夜市规则识别
- **WHEN** POI 名称或类型明确表示夜市
- **THEN** 系统将其标记为 `meal_scene=dinner` 和 `semantic_source=rule`

#### Scenario: 模型补充未知语义
- **WHEN** 规则无法判定用餐场景且 LLM 返回合法枚举
- **THEN** 系统接受该枚举并记录 `semantic_source=llm`

### Requirement: 确定性选择景点子集和路线
系统 SHALL 使用 OR-Tools 确定性模型联合选择景点、跨天分配、访问顺序和开始时间，并 MUST 在全部用户硬约束下求解。

#### Scenario: 多日候选池求解
- **WHEN** 候选景点多于行程可容纳数量
- **THEN** 求解器选择一个满足数量、时间窗、开放时间和跨天唯一性的子集并生成逐日时刻表

#### Scenario: 快速可行解
- **WHEN** 求解器在1秒内获得可行但未证明最优的结果
- **THEN** 系统保存 `FEASIBLE`、Objective、Best Bound、Gap 和求解耗时且不得声称数学最优

### Requirement: 用餐场景参与时段优化
系统 SHALL 将午餐窗口定义为11:30–13:30、晚餐窗口定义为17:30–20:00，并 SHALL 在用餐场景景点与适用窗口至少重叠45分钟时记录对应 coverage。

#### Scenario: 小吃街覆盖晚餐
- **WHEN** `meal_scene=either` 的小吃街在晚餐窗口内游览至少45分钟
- **THEN** 求解结果记录 `meal_coverage=dinner` 并获得配置中的用餐匹配收益

#### Scenario: 用餐场景服从硬约束
- **WHEN** 用餐场景偏好与预约或开放时间硬约束冲突
- **THEN** 求解器优先满足硬约束并允许该景点产生软惩罚或不被选择

### Requirement: 版本化可重算评分
系统 SHALL 通过不可变版本配置定义景点收益和路线惩罚，并 MUST 使用独立 evaluator 重算求解结果的评分明细。

#### Scenario: 保存评分版本
- **WHEN** 规划成功
- **THEN** Run 与 itinerary 保存评分配置版本、候选池 fingerprint、各评分组件和 Solver 诊断

#### Scenario: 调整评分权重
- **WHEN** 开发者需要调整软约束或系数
- **THEN** 系统使用新的配置版本且历史规划继续引用原版本

### Requirement: 分层求解降级与质量门
系统 SHALL 在严格求解无解时只放宽系统每日最小数量，并 SHALL 让所有 Solver、兜底和 Repair 结果通过同一个确定性 Quality Gate。

#### Scenario: 系统最小数量不可行
- **WHEN** 开放时间或候选数量导致默认每日最小数量不可行
- **THEN** 系统可把最小数量降至1、记录 `RELAXED_DAILY_MIN`，但不放宽用户硬约束

#### Scenario: 所有路径均不可行
- **WHEN** 二次求解、确定性兜底和一次 Candidate Repair 后仍无合法结果
- **THEN** Planning Run 以 `planning_quality_failed` 失败且不保存 itinerary
