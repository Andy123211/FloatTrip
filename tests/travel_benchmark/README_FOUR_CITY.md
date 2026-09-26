# 四城三组对照

实验根目录：`artifacts/travel-benchmark/four-city-v2/`。生产默认仍为 A；通过门槛后才允许切换。设置 `PLANNING_VARIANT=B` 使用硬约束修复，`PLANNING_VARIANT=C` 使用 LLM 候选、高德核实及交通排程。

A 的 `app/` 来自修改前完整工作区快照，包含当时未提交修改。B/C 来自开发验证结束后的工作区。每组在独立冻结目录执行，共享已记录的 Python 依赖环境；数据库、测试用户和缓存命名空间隔离。参考答案仅在评测器中读取，不注入生产请求。

```bash
# 已初始化的实验只续跑，不能再次初始化或改写冻结代码。
.venv/bin/python -m tests.travel_benchmark.experiment run
# 可以在执行期间生成已经完成的匿名材料；编号不会随完成进度变化。
.venv/bin/python -m tests.travel_benchmark.experiment packets
# Codex完成blind_reviews后才形成最终质量结论。
.venv/bin/python -m tests.travel_benchmark.experiment freeze-reviews
.venv/bin/python -m tests.travel_benchmark compare --out artifacts/travel-benchmark/four-city-v2
# 每组客观证据回放示例，不调用模型或高德。
.venv/bin/python -m tests.travel_benchmark replay --out artifacts/travel-benchmark/four-city-v2/runs/A
```

矩阵：4城×12场景×3次×3组=432次主链路；4城×2场景×3组=24次对话。单次上限600秒。终结失败不重跑覆盖，未结束记录在恢复时标为interrupted并留在分母。

每个案例的三组按固定种子平衡执行顺序。每次运行使用独立冷缓存命名空间，避免之前试次/生产缓存对某一组有利；试次内正常使用缓存。真实HTTP结果、请求时长、模型usage（服务返回时）、阶段数据和源码漂移检查均保存。

上海南京可用于开发；北京重庆在正式冻结前不执行生产调试。48例及69个实体的来源和hash见 `data/four_city_v2/`，原上海南京24例请求保持完全一致。

`blind_packets/` 隐藏组别与内部总分；`blind_mapping.json` 仅供完成评分后的汇总归因，评阅时不读取。Codex手工逐条判断四维0–5分，不使用脚本自动赋主观分。`compare`按案例聚类bootstrap，不把三次重复当成三个独立用户。

默认切换门槛：C四城各至少29/36通过，C主链路及对话无确认硬违规；北京重庆均分和通过率分别严格高于A，上海南京不得退化。切换必须另存决定记录；compare只计算门槛，不修改配置。

开发试次位于 `dev-*`，正式试次位于 `runs/{A,B,C}`。开发中失败均保留，但不混进预先冻结的正式矩阵。交通服务提供当前估计，不保证旅行日期实际时刻；开放、预约、无障碍缺证据时保留待核实状态。成本仅报告实际观察到的token和API调用，不推算未核实价格。

2026-09-19 用户要求节约高德月度额度后，缓存策略已版本化调整：第 1–415 次保留上述冷缓存政策；第 416 次起以冻结的 `cache_policy_v1/cache_worker.py` 包装原 worker，三组共用本地精确请求缓存，复用已有真实成功响应。原三组算法文件与数据冻结不变。详情见 `docs/amap-cache.md` 与实验目录 `cache_policy_v1/policy.json`。跨缓存分段的延迟、调用量和数据变化必须分别解释；实际网络调用统计排除本地命中。4 次沙箱连接失败保留在正式分母，事故见 `cache_policy_v1/network_incident.json`。
