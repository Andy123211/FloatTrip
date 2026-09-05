## MODIFIED Requirements

### Requirement: Planning Run 冻结有效约束
系统 SHALL 在提交 PlanningBrief 时将显式约束、应用事实、画像 revision、有效约束、覆盖状态、候选语义 catalog 版本和 active scoring profile 写入不可变 Run snapshot，并保持重复提交幂等。

#### Scenario: 提交已确认 brief
- **WHEN** 用户提交 ready brief
- **THEN** 创建且仅创建一个携带确认时约束快照、候选语义版本和评分版本的 Travel Plan Run

### Requirement: 规划节点按类别消费约束
系统 SHALL 将景点、餐饮、预算、节奏、作息、同行、无障碍和交通约束提供给相关规划节点，而不是把未筛选画像整体作为提示文本；核心景点求解 MUST NOT 消费餐厅候选，饮食约束只提供给独立 Restaurant Task。

#### Scenario: 饮食硬约束
- **WHEN** 有效约束要求避开花生
- **THEN** 独立餐厅检索和推荐收到该要求，无法从数据验证时结果标记为 `unverified`，核心景点 Solver 不因此改变路线

#### Scenario: 负向景点约束进入规划
- **WHEN** 有效约束包含 polarity 为 `avoid` 的景点“老门东”
- **THEN** 候选构建和确定性 Solver 将“老门东”排除，而不是把它作为普通景点偏好

#### Scenario: 住宿偏好缺少数据源
- **WHEN** 有效约束包含住宿偏好但系统没有酒店数据源
- **THEN** 系统将其标为 advisory 且不声称已完成酒店推荐或预订
