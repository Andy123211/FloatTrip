## MODIFIED Requirements

### Requirement: 语言理解与业务执行分离
系统 MUST 将主 Agent 的语言理解和工具选择与确定性业务执行分离；`decision` 路径与 `react` 工具路径必须复用相同的所有权、字段、readiness、幂等和状态机校验服务。

#### Scenario: Agent 请求更新 Brief
- **WHEN** 主 Agent 调用 `update_current_brief` 并提供结构化 patch
- **THEN** 确定性服务校验并写入 patch，而不是由模型直接修改数据库

#### Scenario: 旧模式仍在运行
- **WHEN** `CHAT_AGENT_MODE=decision`
- **THEN** `DialogueDecision` 的动作继续通过同一确定性服务执行

### Requirement: 资源引用必须权威校验
系统 MUST 对工具或旧 DialogueDecision 引用的 itinerary、Run、Brief 与候选目标进行数据库所有权和当前 Conversation 作用域校验，不能相信模型生成的 ID。

#### Scenario: Agent 引用了不存在或越权的 itinerary
- **WHEN** 工具参数包含不存在或不属于当前用户的 itinerary ID
- **THEN** 系统拒绝读取或修改且不泄露该资源是否属于其他用户

#### Scenario: 修改目标不唯一
- **WHEN** 用户表达修改意图但没有唯一的显式或绑定目标
- **THEN** 系统不创建 Revision Run，并返回需要澄清的安全结果

### Requirement: 正式规划需要结构化明确确认
系统 MUST 在 `submit_current_brief` 中同时验证当前用户指令的明确性、唯一 active Brief 的 readiness 与重复提交状态，不能仅依赖模型是否调用了工具。

#### Scenario: 用户明确要求开始规划
- **WHEN** 当前消息明确要求生成方案且 Brief 已 ready
- **THEN** 系统创建或返回唯一的 `TRAVEL_PLAN` Run

#### Scenario: Agent 在普通讨论中调用提交工具
- **WHEN** 当前消息不包含明确生成指令
- **THEN** 工具拒绝创建 Run

### Requirement: 确定性字段校验不得演变为语言识别
系统 SHALL 继续使用字段类型、日期范围、枚举、必填项和资源状态等确定性规则校验工具输入，不得在执行层新增基于关键词的第二套通用意图路由。

#### Scenario: Brief patch 包含非法日期
- **WHEN** Agent 提交结束日期早于开始日期的结构化 patch
- **THEN** 执行层按字段规则拒绝 patch

### Requirement: Run 控制遵守状态机与确认边界
`control_run` MUST only support `cancel` and `retry`, MUST validate ownership and legal state transitions, and MUST require an explicit current-user instruction for cancellation.

#### Scenario: Agent 尝试取消未明确指定的任务
- **WHEN** 当前消息没有明确取消指令或目标 Run 不唯一
- **THEN** 工具不改变任何 Run 状态

#### Scenario: Agent 重试不可重试的 Run
- **WHEN** 目标 Run 当前状态不允许 retry
- **THEN** 系统拒绝该转换并返回安全状态摘要
