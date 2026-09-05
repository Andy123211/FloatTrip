## ADDED Requirements

### Requirement: 餐厅推荐与核心景点规划解耦
系统 SHALL 仅在用户明确请求时运行独立餐厅推荐，并 MUST NOT 让餐厅候选改变核心景点子集、日期、顺序或时间。

#### Scenario: 核心规划完成
- **WHEN** 景点规划通过 Quality Gate
- **THEN** 系统保存只含 attraction 的核心 timeline，且不自动运行餐厅搜索

### Requirement: 用餐覆盖阻止重复餐厅安排
Restaurant Task SHALL 读取核心路线的 `meal_coverage`，并 MUST NOT 为已被用餐场景景点覆盖的餐次推荐外部餐厅。

#### Scenario: 夜市已覆盖晚餐
- **WHEN** 某天 attraction timeline 标记夜市覆盖 dinner
- **THEN** 餐厅结果将 `external_restaurant` 设为空，并可返回该街区的代表性吃法建议

#### Scenario: 餐次未覆盖
- **WHEN** 某天午餐或晚餐没有 attraction coverage
- **THEN** Restaurant Task 按最终路线锚点搜索并推荐餐厅

### Requirement: 餐厅 enrichment 独立持久化
系统 SHALL 按 itinerary 和 route fingerprint 独立保存餐厅 enrichment，并 SHALL NOT 修改核心 `plan_json`。

#### Scenario: 核心路线被编辑
- **WHEN** 景点名称、顺序、时间或坐标变化导致 route fingerprint 改变
- **THEN** 系统不再投影旧餐厅 enrichment，并在新请求时重新生成
