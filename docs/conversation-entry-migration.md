# 对话式规划入口说明

## 默认入口

- 顶部“旅行对话”进入最近一个未归档对话。
- 未登录用户完成登录后继续原动作；关闭登录弹窗不会创建对话或跳转。
- PlanningBrief 确认动作直接创建后台 Planning Run。

## 修改已有行程

- 行程详情中的修改意见会绑定当前 itinerary 并作为 Conversation Message 提交。
- 若没有未归档对话，客户端先创建一个新对话。
- Chat Run 通过确定性动作边界创建 Revision Run；修改不会覆盖原行程。
- Revision 需要确认时进入 `waiting_user`，继续使用 Run interaction 与 checkpoint 恢复。
- 登录前发起的修改会保存为认证 continuation，登录后自动提交。

## 失败与恢复

- 客户端用一次性 nonce 防止界面 effect 重复提交同一修改。
- 自动提交失败时保留修改文字和目标 itinerary，用户可在 composer 中重试。
- Revision 的取消、重试、刷新恢复和结果打开统一使用 Runtime Run API。
