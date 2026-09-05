## Context

FloatTrip 当前的 Chat Run 只调用一次结构化 `DialogueDecision`，再由 `DialogueActionExecutor` 执行业务动作。Planning 与 Revision 已经具备独立 LangGraph、Runtime Run、重试取消和 SSE，因此本次改造只替换 Chat Run 的编排方式，不重写旅行规划链路。

Conversation 数据库仍负责跨轮消息、摘要、Brief、绑定行程和冻结记忆；LangGraph checkpoint 只保存单个 Chat Run 内的 ReAct 循环。这个边界避免数据库状态和 checkpoint 状态互相覆盖，也允许在不迁移历史会话的情况下按环境变量回退。

## Goals / Non-Goals

**Goals:**

- 以 LangChain `create_agent` 提供可流式输出、可调用受控工具的主 Agent。
- 让正式规划与修改继续作为独立后台 Run 执行，主 Agent 创建 Run 后即可结束当前回复。
- 提供严格按当前用户、Conversation 和冻结记忆限定范围的查询与动作工具。
- 持久化方案卡片附件，并向 Web 提供可刷新恢复的展示数据。
- 将工具生命周期转换成安全的产品化 SSE 事件，并记录缓存与 Agent 运行指标。
- 通过 `CHAT_AGENT_MODE=decision|react` 保持一个迁移周期的即时回退能力。

**Non-Goals:**

- 不引入 Deep Agents，也不增加同步研究/比较子 Agent。
- 不改变现有 Planning、Revision 图的核心生成逻辑和 Run 状态机。
- 不让模型直接填写用户 ID、Conversation ID、Chat Run ID 或记忆 revision。
- 不在第一阶段为移动端实现附件 UI；移动端只保持协议类型兼容。

## Decisions

### 主 Agent 的状态边界

新增 `MainAgentContext`，由 Runtime 注入 `user_id`、`conversation_id`、`chat_run_id` 和冻结记忆 revision。工具只从 `ToolRuntime.context` 取得这些字段，任何公开参数都不接受身份或作用域字段。

每个 Chat Run 使用自己的 `run_id` 作为 checkpoint thread。跨轮输入从数据库确定性重建，顺序为：固定系统提示词、固定工具定义、冻结记忆、会话摘要、最近消息和当前消息。日期、Brief、活动 Run 与绑定行程不再注入稳定前缀，而由 `get_planning_context()` 按需读取。

### 工具与确定性业务服务

主 Agent 暴露八个工具：冻结记忆查询、动态规划上下文、历史方案搜索、单方案读取、Brief 更新、Brief 提交、发起修改以及 Run 控制。查询和 Brief 更新可直接执行；正式规划、取消和修改必须能从当前用户消息中找到明确指令。

工具不复制业务规则。Brief patch、readiness、所有权、可修改 checkpoint、重复提交及 Run 状态转换继续由确定性服务校验。旧 `DialogueActionExecutor` 也调用相同服务，使 `decision` 和 `react` 两条路径行为一致。

`find_saved_itineraries` 使用 `content_and_artifact`：模型只获得精简摘要，应用获得类型化 `itinerary_collection`。默认按 `root_id` 去重，仅保留最高版本；精确结果与近似结果分开，最多返回五条。

### 后台 Planning 与 Revision

`submit_current_brief()` 创建或返回当前 Brief 对应的 `TRAVEL_PLAN` Run；`start_revision()` 创建独立 `REVISION` Run。工具只返回 Run 摘要，不等待规划完成。现有 worker、取消、重试、恢复与最终行程持久化保持不变。

### 消息附件

`messages` 增加默认空数组的 JSON 字段。每条消息最多五个附件，每个方案集合最多五张卡。卡片只保存打开详情所需的稳定元数据与少量亮点，不保存完整 `plan_json`。Assistant 完成消息以单次数据库写入同时保存文本和附件，SSE 完成事件返回同一对象。

Web 在原回复下渲染可键盘操作的方案卡，点击后复用 `/api/history/{itinerary_id}` 打开完整方案。旧消息缺少附件时按空数组处理；移动端安全忽略附件。

### 流式事件与安全

Runtime 增加 LangGraph `tools` stream。原始工具事件在服务端按白名单转换为 `agent.activity.started|completed|failed`；工具内部通过 `ToolRuntime.stream_writer` 发送 `agent.activity.progress`。`activity_id` 从 `tool_call_id` 派生，以便前端聚合。

生命周期事件持久化以支持运行中重连，高频 progress 和 token delta 只实时发送。公开 payload 只含固定活动类型、固定文案、阶段及安全统计，不包含原始工具名、参数、结果、记忆正文或推理过程。

### 渐进迁移与观测

初始默认 `CHAT_AGENT_MODE=decision`。`react` 复用同一 ChatService、数据库和 Runtime 事件协议；回归与评测通过后再切默认值。记录主 Agent 轮次、工具调用数、延迟、失败原因以及 provider cache hit/miss token；指标缺失时不得影响回复。

## Risks / Trade-offs

- `create_agent` 的消息状态可能与数据库历史重复：以每 Run checkpoint 和数据库重建输入隔离，禁止 Conversation 级 checkpoint。
- 工具结果可能扩大上下文：所有列表限量，单方案默认只返回摘要，完整天级内容必须显式指定 `day`。
- 模型可能对隐含意图执行高影响动作：工具再次检查当前消息中的显式意图，目标不唯一时返回可澄清错误。
- 附件事件与消息写入可能重复：数据库消息是恢复真相，前端按消息 ID 和附件稳定键去重。
- LangGraph 版本的 tools stream 形态可能变化：映射层容忍未知事件并忽略未白名单字段，旧 decision worker 不受影响。

## Migration Plan

1. 先迁移消息表并让所有 API 默认返回 `artifacts: []`。
2. 增加确定性工具服务、React 图和安全流式映射，但保持 decision 默认。
3. 上线 Web 卡片与移动端协议兼容。
4. 在测试和灰度环境启用 react，比较行为、缓存和失败指标。
5. 回归通过后另行切换默认值；本变更不删除旧路径。

回滚只需设置 `CHAT_AGENT_MODE=decision`。数据库新增列和附件字段均向后兼容，无需回滚数据。

## Open Questions

- provider 对缓存 token 的字段命名可能不同，指标适配器将保留未知字段并在实际模型响应上补充映射。
- 同步研究/方案比较 Agent 的 `task` 工具留待后续独立变更。
