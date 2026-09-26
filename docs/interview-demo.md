# 上海本机面试演示

> 最新配置：按用户要求，现场演示模式已解除本地累计50次上限。缓存和累计计数继续保留；下文50次及剩余额度为此前验收时的历史记录。高德服务端自身的配额仍生效。

演示版已独立接入现有产品网页，版本为 C5＋通用地点匹配修复。未切换正式默认 A。

```bash
.venv/bin/python scripts/interview_demo/run.py
```

访问 http://localhost:8766 。完整账号、演示步骤、实际结果和限制见：

[演示说明](../artifacts/interview-demo/shanghai-c5-match-v1/演示说明.md)

实现入口：`scripts/interview_demo/`。运行产物及冻结应用：`artifacts/interview-demo/shanghai-c5-match-v1/`。

演示额度累计最多50次，当前演练用了19次。只要保留 `data/budget.sqlite`，重启不会重置额度。预算保护由底层HTTP请求逐次原子计数执行，缓存命中不计。不要删除演示数据目录或重建预算。

如需重算本地验收证据（不联网）：

```bash
.venv/bin/python scripts/interview_demo/audit.py
```

原产品仍可用 `PLANNING_VARIANT=A .venv/bin/python run.py` 启动，端口8765。演示用独立账号、数据库、检查点和缓存，不覆盖旧实验。API密钥仅从项目现有配置读取到进程环境，未复制到交付产物。
