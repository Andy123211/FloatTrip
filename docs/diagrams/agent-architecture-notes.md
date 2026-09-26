# FloatTrip Agent 架构说明

依据当前工作区代码绘制（2026-09-19），包含本地尚未提交的实现。图展示默认 `CHAT_AGENT_MODE=react` 路径；部署环境可显式改为 `decision`，本次未读取私密环境配置。

## 主子分工

| 角色 | 责任 | 实现 |
|---|---|---|
| 主 Agent | ReAct 对话；澄清需求、维护 PlanningBrief、选择受控工具、解释结果 | `app/chat/react_graph.py:12`；`app/chat/prompts.py` |
| Planner task | 接收确认后的 Brief，在当前 Chat Run 内执行新规划图，返回行程 ID | `app/chat/service.py:121` |
| Candidate Builder | LLM 为真实 POI 补充候选语义；服务端绑定、过滤并兜底 | `app/planning/nodes.py:286`；`app/planning/candidate_builder.py` |
| 优化器和质量门禁 | 确定性选择景点、排序和排程，独立复算校验；不是 LLM Agent | `app/planning/nodes.py:351`；`app/planning/nodes.py:403` |
| Revision task | 读取原行程状态及修改意见，执行修改图，返回新版本 ID | `app/chat/service.py:190` |
| Planner / Reviewer | Planner 改写路线，Reviewer 评审并反馈；通过或到轮数上限后进入餐饮阶段 | `app/planning/graph.py:154` |
| 餐饮推荐 | 先调用高德搜索周边餐厅，再由 LLM 逐日选择；当前属于修改图 | `app/planning/graph.py:168` |
| 配套服务 | 行程落库后异步补充游玩贴士；会话摘要和长期记忆提取提供上下文 | `app/planning/runtime_worker.py`；`app/chat/memory_service.py` |

## 实际执行链

新规划：天气查询 → 高德景点搜索 → Candidate Builder → 确定性优化器 → 质量门禁 → finalize → 行程落库。

质量门禁失败时默认允许 1 次候选修复，回到 Candidate Builder 后重新求解；再次失败则抛出错误。

修改：Planner → revision_concern（有疑虑时请求用户补充）→ Reviewer → 餐厅搜索 → 餐饮推荐 → finalize → 新版本落库。Reviewer 未通过且未达轮数上限时回到 Planner；达到上限继续收敛并不等于评审通过。

主 Agent 通过 `submit_current_brief` / `start_revision` 调用两个子任务。默认 ReAct 路径中，它们在当前 Chat Run 内执行，不创建独立的子 Run。子图内部流程由 LangGraph 固定编排；主 Agent 不直接选择每个内部节点。进度由 Runtime 发布到界面，子任务结束后将行程 ID 作为工具结果返回。

主 Agent 还有 6 个普通工具：`get_travel_memory`、`get_planning_context`、`find_saved_itineraries`、`get_saved_itinerary`、`update_current_brief`、`control_run`。这些是服务端业务操作，不是独立 Agent。

Runtime 管理排队、并发、持久事件、取消重试和 interrupt 恢复。默认主图接入 SQLite checkpointer；工具内部构建的规划/修改子图没有显式传入 checkpointer，不能据此宣称每个子节点都能独立恢复。另有注册到调度器的独立规划/修改 Worker 路径。

## 与旧文档的差异

README 中的 Planner ⇄ Reviewer → Time Check 描述不是当前新规划 `build_graph()` 的拓扑。当前新规划已采用候选生成与算法求解；Planner / Reviewer 仍用于修改图。Time Check 函数仍在代码中，但当前这两张图均未接入。新规划的开放时间和闭馆规则由优化器及校验逻辑处理。

## 图表交付记录

- diagram_type: architecture
- output: /Users/chj/Desktop/new tripagent/docs/diagrams/agent-architecture.html
- specification_sha256: 8286c1dafe5a8b3d7b349f29e67ffa837051c28a8956409c90b6ff36bb7141c9
- artifact_sha256: 6d684183c5f4a4cf0cdd12c7b887de5747a70bc129c4d105aff369762f7bc7e9
- validation: 9/9 showcase, 0 errors, 0 warnings
- browser_evidence: passed
- visual_review: passed
- correction_rounds: 2

浏览器检查覆盖 1440×900、1600×1000、1920×1080、2048×1320，均无页面溢出。人工视觉检查由图像工具完成，查看 2048×1320 浅色与 1440×900 深色截图，确认节点、连线、标签、说明卡片无明显遮挡。视觉检查不等于逐项测试搜索、聚焦和导出交互。
