## MODIFIED Requirements

### Requirement: Defined lifecycle transitions
The system SHALL expose Run status using `queued`, `running`, `waiting_user`, `succeeded`, `failed`, or `cancelled`, and SHALL persist each transition. A formal planning Run MUST NOT succeed unless its deterministic Quality Gate passes.

#### Scenario: Successful execution
- **WHEN** a queued run is admitted, executes, passes its required Quality Gate, and produces its required result
- **THEN** its status transitions through running to succeeded

#### Scenario: Execution failure
- **WHEN** an unhandled provider, graph, persistence, solver, or quality error prevents valid result production
- **THEN** the Run becomes failed with a sanitized user-visible error and an internal diagnostic record, and no invalid itinerary is committed

## ADDED Requirements

### Requirement: 独立餐厅任务生命周期
系统 SHALL 将按需餐厅推荐表示为独立可重试 Run，并 SHALL 关联其 owner、目标 itinerary 和 route fingerprint。

#### Scenario: 用户请求餐厅推荐
- **WHEN** 用户对已保存 itinerary 明确请求餐厅推荐
- **THEN** 系统创建或复用同一 route fingerprint 的餐厅任务，且其失败不改变核心 itinerary 的成功状态
