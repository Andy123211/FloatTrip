## Why

当前旅行对话只进行一次结构化 LLM 决策，无法在同一轮内自主读取记忆、查询历史方案、更新需求并派发后台规划。仓库已经具备持久化 Run、冻结记忆和独立 Planning/Revision Graph，现在需要用受控 ReAct 主 Agent 把这些能力组织成连续、流式且可恢复的用户体验。

## What Changes

- 使用 LangChain `create_agent` 增加可渐进启用的流式 ReAct 主 Agent，保留旧 `DialogueDecision` 路径作为一个迁移周期的回退。
- 将长期记忆、动态规划上下文、保存方案查询、Brief 更新、正式规划、修改和 Run 控制暴露为所有权受控的 Agent tools。
- 正式规划和行程修改继续创建独立后台 Run，由现有 Planning/Revision Graph 执行，不阻塞主 Agent 对话。
- 增加产品化工具活动 SSE，支持通用工具生命周期和工具内部进度，同时禁止暴露参数、结果正文和模型推理。
- 为 Conversation Message 增加类型化附件；历史方案搜索结果以可持久恢复的方案卡片展示，并复用现有行程详情 API。
- 保持同一 Conversation 的长期记忆快照冻结，并调整 Prompt 布局和指标以提升、观察 DeepSeek prefix cache 命中。

## Capabilities

### New Capabilities

- `main-agent-orchestration`: 定义主 Agent 的上下文、工具调用、后台 Run 委派、安全边界和渐进迁移。
- `conversation-message-artifacts`: 定义 Assistant Message 类型化附件、历史方案卡片和刷新恢复行为。
- `agent-activity-streaming`: 定义 Token、工具生命周期、工具内部进度与公开 SSE 投影。

### Modified Capabilities

- `conversational-planning`: 规划需求补齐、Brief 更新和正式规划确认改由主 Agent 通过受控工具完成。
- `validated-dialogue-actions`: 从单一 `DialogueDecision` 执行器扩展为模型选择工具、确定性服务校验并执行的边界。
- `conversation-memory`: 冻结长期记忆成为主 Agent 稳定前缀，同时提供只读取冻结快照的查询工具。

## Impact

- 后端涉及 `app/chat`、`app/runtime`、Conversation Message 数据模型和 SQLite 增量迁移。
- 公共 Conversation Message 增加向后兼容的 `artifacts` 字段，SSE 增加 `agent.activity.*` custom events。
- Web 对话时间线增加方案附件卡；移动端只更新 OpenAPI 类型并忽略未知附件 UI。
- 显式增加 LangChain 依赖约束，不引入 Deep Agents；Planning/Revision Runtime 和已有历史详情 API 保持兼容。
