# C2 可靠性修复

默认仍为 A。`PLANNING_VARIANT=C` 使用当前 C2；C1 为实验目录的不可变修改前工作区快照。模型、温度和优化评分权重不变。

C2 把同一高德实体的候选合并，保留独立父子目的地，过滤入口和办公部门。模型别名不是独立的身份事实。已知公共开放信息转换为按日区间、闭馆和停止入场限制；不支持的来源格式、例外和缺失信息标为待核实。相同区间供 CP-SAT、后备排程和最终验收使用。

交通证据按方向、实体和请求交通方式保存，每次重新排程都写回候选；初排后最多两轮补查/重排。重新优化整个候选集合，不把可选景点提升成必去。不查询全量两两矩阵。最终未查询到的边逐条标待核实；已知不足的转场不能通过。

最终行程增加 `validation_summary` 与 `pending_checks`，旧字段保持兼容。状态为 `supported_checks_passed`、`needs_verification` 或 `failed`；最后一种不成功交付。通过所支持检查不保证未来交通、预约或无障碍条件。无来源采集时间时明确记为未知，不使用当前时间冒充原采集时间。

离线测试：

```bash
.venv/bin/python -m pytest tests/test_planning_reliability.py tests/test_grounded_planning.py tests/test_attraction_optimizer.py tests/test_amap_cache.py tests/travel_benchmark/test_evidence.py tests/travel_benchmark/test_experiment.py tests/test_runtime_e2e.py -q
```

对照入口（初始化只允许一次）：

```bash
.venv/bin/python -m tests.travel_benchmark.reliability_pilot initialize
.venv/bin/python -m tests.travel_benchmark.reliability_pilot run
.venv/bin/python -m tests.travel_benchmark.reliability_pilot packets
.venv/bin/python -m tests.travel_benchmark.reliability_pilot freeze-reviews
.venv/bin/python -m tests.travel_benchmark.reliability_pilot compare
```

实验使用上海南京12案例、每版本3次，加8次对话，共80次；共享独立缓存且保留原TTL。联网前原子预占额度，异常也计数，累计500次停止派发。已发起但被预算中断的试次单独保留为 `budget_interrupted`，不标算法失败；不得覆盖终态。尚未派发试次不进入已完成分母，不完整实验不得通过扩大验证门槛。

评分必须逐次由 Codex 完成，脚本不生成主观分。参考库的游览实体与热门分组分别处理：街区与其地标楼、河流与沿岸景区不能仅因合属一个热门组就判重复硬违规。纠正仅在新实验冻结版本应用到两版本，旧实验不修改。明确重复安排仍是硬违规；同区域重复覆盖的价值另按路线内容人工评阅。

本轮已完成：77 项离线回归通过，80 次线上试次与盲评完成，高德实际请求 494/500 次。C2 未达到质量门槛，默认保持 A，不进入扩大验证。上海主链路通过率 C1 10/18、C2 7/18；南京 C1 15/18、C2 9/18。完整归因与证据见 `artifacts/travel-benchmark/c2-reliability-v1/FINAL_REPORT.md`。不要在此冻结实验上继续修复或改分。

离线核对全部记录、代码与评分封存（不调用模型或高德）：

```bash
.venv/bin/python -m tests.travel_benchmark.pilot_audit
```
