## 1. 基础契约与配置

- [x] 1.1 增加 OR-Tools 依赖、候选景点 Schema、Solver 结果与质量报告模型
- [x] 1.2 保留高德 `type/typecode` 并实现用餐场景规则识别与语义合并
- [x] 1.3 增加不可变 `balanced-v1` 评分配置、加载校验和评分明细重算

## 2. 确定性子集求解

- [x] 2.1 实现 CP-SAT 景点选择、跨天分配、可选路径和时刻表变量
- [x] 2.2 实现用餐窗口 coverage、评分目标、Solver 诊断和1秒求解参数
- [x] 2.3 实现每日最小数量放宽、聚类贪心兜底和确定性 Quality Gate

## 3. 规划图接入

- [x] 3.1 将 Planner 改为候选池生成并以权威 POI 重新关联模型输出
- [x] 3.2 将子集优化、一次 Candidate Repair 和质量失败路由接入正式 Planning Graph
- [x] 3.3 更新 finalize、checkpoint 和手动优化兼容投影，核心 timeline 仅输出 attraction

## 4. 餐厅 Enrichment

- [x] 4.1 增加 route fingerprint 和独立 restaurant enrichment 持久化
- [x] 4.2 将餐厅推荐改为按需流程，并跳过已被 attraction `meal_coverage` 覆盖的餐次

## 5. 验证与交付

- [x] 5.1 补齐 POI 语义、评分、全局小样本最优、coverage、硬约束和降级单元测试
- [x] 5.2 补齐 Planning Graph、餐厅解耦、历史结果和手动编辑回归测试
- [x] 5.3 运行目标测试与完整测试套件，验证 OpenSpec 变更并记录已知兼容限制

## 6. Provider 兼容补充

- [x] 6.1 将显式 Think 的 DeepSeek 结构化调用改为“Think 语义草稿 + 非 Think Strict Function Calling”两阶段，并补充同步/异步回归测试
