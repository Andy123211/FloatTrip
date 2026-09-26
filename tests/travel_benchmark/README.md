# 上海、南京独立旅游 Benchmark

与 `tests/eval/` 的旧 Planner/Reviewer 评测并行存在。本工具运行当前生产 `build_graph`，不调整规划算法。

```bash
.venv/bin/python -m pytest tests/travel_benchmark/test_evidence.py -q
.venv/bin/python -m tests.travel_benchmark run --smoke
.venv/bin/python -m tests.travel_benchmark run --k 3
.venv/bin/python -m tests.travel_benchmark run --mode dialogue
.venv/bin/python -m tests.travel_benchmark replay
.venv/bin/python -m tests.travel_benchmark report
```

本次完整结果在 `artifacts/travel-benchmark/2026-09-19-live/`，使用 `--out` 指定该目录。
最初沙箱DNS失败的两次独立预检保留在 `artifacts/travel-benchmark/2026-09-19/`，不能混称为模型规划失败。

支持 `--city 上海`、`--case sh-history`、`--out /absolute/path`。首次 run 冻结数据、规则、源文件哈希；后续同目录续跑拒绝代码/数据变化，已终结试次不覆盖。新实验用新目录。

已有两份交付实验不会被重跑覆盖。再次实测请给上述所有命令追加同一个新的 `--out artifacts/travel-benchmark/new-baseline`。交付版本进一步在每个 worker 前后核对文件哈希；本次实测的版本边界及运行后工作区改动见完整结果的 `version_audit.json`。

24例×3次主链路，另4例对话；两城 `first-1d` 首次运行是冒烟并计入正式试次。每次独立进程，串行，600秒超时；中断、服务失败也保留在统计分母。

输入固定为2026-10-13开始。这个日期超出高德短期天气预报时，保留生产默认的未知天气处理，不构造晴天。长期重跑需发布新版日期数据，不在既有实验内修改。

`data/` 是只供评价的参考库，不传进生产图；`input` 仅含用户明确需求。记忆主链路案例输入已确认约束，真正的模型记忆投影另由4例对话覆盖。对话用独立SQLite与构造用户；完整自然语言与模型工具交互保留。对话中的行程后处理贴士与影子优化不在范围，评测侧关闭这两个后台任务，核心规划及最终落库原样执行。

源数据将官方城市代表性、主题用途和平台热度作为不同证据，编辑分层不是官方排行榜。参考游览时长与适用人群为编辑判断。未知景点不自动认作冷门；未确认条件明确列出。

`replay` 无网络复算客观指标并与已存证据比对。主观分只能由Codex按 `RUBRIC.md` 逐例写入；未评分时报告显示待评，不冒充完成。

`replay --optimizer --case sh-late` 额外用冻结候选、日期和天气重跑生产优化器，结果保存在 `optimizer_replays/`；不调用模型或高德。固定随机种子也不保证壁钟限时求解每次终止于同一解。

审阅中发现的别名错误写入有来源的 `identity_adjudications.json`，不改冻结参考库、请求或阈值。`replay --refresh-evidence` 将旧指标归档到带时间戳的 `evidence_revisions/`，记录新版评价器哈希后复算；之后普通 `replay` 必须严格匹配。1.1版还修正了未知地点被误认为同景区、以及把父景区避雷扩大到独立子景点的问题。

产物：`manifest.json`、`frozen/`、`schedule.json`、`trials/`、`evidence/`、`review_packets/`、`reviews/`、`summary.json`、`REPORT.md`、`FINDINGS.md`。SQLite是隔离运行的本地产物。真实调用需要现有 `.env.local` 的高德/DeepSeek配置，任何密钥不写进产物。
