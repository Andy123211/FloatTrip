# Agent Runtime 运行说明

## 架构边界

依赖方向固定为：

```text
FastAPI / SSE
      ↓
Application Services
      ↓
Agent Runtime ─────→ SQLite / LangGraph Checkpointer
      ↓
Chat Graph / Planning Graph
```

`app/chat` 与 `app/planning` 不允许导入 FastAPI、`StreamingResponse` 或前端协议。
图只产生 LangGraph `messages`、`custom` 和内部状态更新；`app/runtime` 负责验证、
持久化和投影公开事件，`app/api` 负责 HTTP/SSE 编码。

## 默认配置

| 环境变量 | 默认值 | 含义 |
|---|---:|---|
| `RUNTIME_CHAT_CONCURRENCY` | 8 | 同时运行的 Chat Run 上限 |
| `RUNTIME_PLANNING_CONCURRENCY` | 2 | 全局正式规划/修改 Run 上限 |
| `RUNTIME_PLANNING_PER_USER` | 2 | 单用户正式规划/修改上限 |
| `RUNTIME_LLM_CONCURRENCY` | 8 | LLM 调用容量 |
| `RUNTIME_AMAP_CONCURRENCY` | 8 | 高德调用容量 |
| `RUNTIME_CHECKPOINT_DB` | `data/langgraph-checkpoints.db` | LangGraph SQLite checkpoint 文件 |
| `CHAT_CONTEXT_BUDGET_TOKENS` | 3000 | Chat 最终上下文预算 |
| `CHAT_SUMMARY_TRIGGER_TOKENS` | 2600 | 触发累计摘要的估算 token 阈值 |
| `CHAT_SUMMARY_TARGET_TOKENS` | 600 | 结构化摘要目标大小 |
| `CHAT_RECENT_TURNS` | 6 | 压缩后保留的完整最近轮次 |
| `CHAT_MAX_MESSAGE_TOKENS` | 1200 | 单条用户消息预算 |
| `CHAT_SUMMARY_MODEL` | 当前 provider 默认模型 | 可选摘要模型覆盖 |
| `MEMORY_EXTRACTION_MODEL` | 当前 provider 默认模型 | 可选长期记忆提取模型覆盖 |
| `MEMORY_EXTRACTION_INPUT_TOKENS` | 6000 | 归档提取的单批输入预算 |

SQLite 主库启用 WAL 与 30 秒 busy timeout。Run 事件序列在单个 Run 内单调递增；
生命周期、进度、等待交互、错误、最终消息和结果关联为持久事件，token delta 与
heartbeat 默认只走实时通道。

## 恢复行为

- `queued` Run 在单节点进程启动后重新进入确定性队列。
- 无本地执行协程的 `running` Run 会被标记为可重试失败，避免永久卡住。
- `waiting_user` Run 保留原交互和 checkpoint，等待用户回复或取消。
- LangGraph interrupt 使用 `run_id` 作为稳定 `thread_id`。恢复必须同时匹配
  `run_id` 和当前 `interaction_id`，然后发送 `Command(resume=...)`。
- SSE 重连先建立实时订阅，再按 `after_seq`/`Last-Event-ID` 回放持久事件，最后
  按序去重切换到实时流。

## 并发与一致性

- 长期记忆提取使用数据库租约领取：每次领取生成独立执行凭证，租约默认 120 秒，
  每 40 秒续租。实例启动不会重置其他实例的运行任务；仅过期任务可被接管。
- 记忆分批写入、旧事实作废和 `applied_through_sequence` 在同一事务提交，事务前后
  校验租约；接管后跳过已提交批次。任务成功、失败、续租和释放均校验当前凭证，
  旧实例的晚到结果不能写回。正常停机释放自身租约；异常退出由租约超时恢复，
  连续异常到达 5 次后进入可手动重试的失败状态。
- 此保证针对共享同一个 SQLite 数据库的新版记忆 worker。租约过期时外部模型请求
  可能重试，不承诺外部调用 exactly-once。升级前须停止所有旧版实例，避免旧 worker
  继续执行无凭证写入或全局重置；新增字段为兼容迁移，旧版无租约运行任务可重新领取。
- Chat：`chat:{conversation_id}`，同一会话串行。
- 新规划：`plan:{run_id}`，彼此独立并受用户/全局容量约束。
- 行程修改：`revision:{itinerary_id}`，同一基础行程严格串行并生成新版本。
- 正式规划任务达到容量时仍可创建，状态保持 `queued`；Chat 使用独立容量，不被
  长规划挤占。

## 单节点限制

除上述记忆提取队列外，当前实时通知、协程任务句柄和信号量位于进程内，不提供多节点任务领取、
exactly-once 执行或跨节点 SSE fan-out。若部署多个应用实例，需要引入外部任务
队列/租约、共享事件流和分布式并发控制；在完成这些工作前应保持单节点运行。

记忆租约回归：`python -m pytest tests/test_memory_leases.py tests/test_conversation_memory.py tests/test_planning_memory.py -q`。
包含独立进程争抢、第二实例启动、持续续租、旧凭证拒写、分批接管、事务回滚、重试上限及旧库迁移。

## 观测

认证用户可读取 `/api/runtime/metrics`，其中包括排队时长、运行时长、各类型活动
Run、终态计数、失败原因、provider 容量，以及会话估算 token、摘要/提取结果和归档
整理延迟。Runtime 同时输出 JSON 结构化日志；记忆日志只记录 conversation、revision、
序列范围、计数和状态，不记录事实正文或原始消息。

### 行程修改的补搜与暂停

Revision 从当前 `plan_json` 的已保存时间线恢复基线，旧 planner checkpoint 仅复用地点资料与偏好；手动删除的地点默认不重新引入。工具入口强制匹配本轮 `related_itinerary_id`。修改前分析搜索需求，涉及替换、热门地标或新类型时调用已有高德搜索；无评分但有真实地点数据的地标仍保留，用户明确点名与系统推荐分别记录。

Revision 子图依次执行日期补充、修改分析、按需搜索、Planner、用户取舍/自动补搜分流、Reviewer、餐饮与汇总。未验证的地点或候选不足转自动补搜，每次修改最多两轮；网络失败和耗尽搜索进入可重试失败，原方案保留，不冒充缺少用户信息。新修改子图继承父 Chat Run 的 checkpoint，并同步保存节点进度；恢复从中断节点继续，不重新执行已完成的搜索。旧等待任务的已接受回复从原响应记录读取，兼容升级前无子图 checkpoint 的情况。成功仍保存为新行程版本。

`tests/test_revision_discovery.py` 覆盖真实 POI 合并、两轮上限、搜索错误、目标校验、手动编辑基线，以及嵌套图暂停后重建恢复且只搜索一次。此改动不把旧 Revision 算法整体迁移为确定性优化器，也不改变主任务调度器的多实例保证。

单节点启动时只将失去执行协程的 `running` 任务标为中断。`waiting_user` 已持久化交互与 checkpoint，重启后保持等待，用户仍可通过原交互 ID 回复或取消；不会自动代答或重新执行搜索。
