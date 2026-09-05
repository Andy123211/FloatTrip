## 1. 数据与协议基础

- [x] 1.1 为 Conversation 消息增加向后兼容的 `artifacts` JSON 持久化、校验和 API round-trip
- [x] 1.2 定义主 Agent 上下文、工具结果、方案集合 artifact 与安全 activity 事件模型
- [x] 1.3 更新 Web 与移动端 API 类型以兼容消息附件和 Agent activity

## 2. 确定性工具服务

- [x] 2.1 实现按冻结 revision 查询 active 记忆的筛选与安全序列化
- [x] 2.2 实现保存方案的最新版去重搜索、近似结果、摘要详情与所有权隔离
- [x] 2.3 抽取并复用 Brief patch、提交、Revision 创建和 Run 控制的确定性校验服务
- [x] 2.4 将八个 LangChain 工具接入 runtime context、限量 artifact 与内部 progress

## 3. 主 Agent 与 Runtime

- [x] 3.1 构建稳定前缀的主 Agent Prompt 和每轮数据库上下文重建
- [x] 3.2 使用 `create_agent` 建立按 Chat Run checkpoint 的 ReAct 图与最终消息持久化
- [x] 3.3 通过 `CHAT_AGENT_MODE=decision|react` 接入 Runtime 并保持 decision 默认回退
- [x] 3.4 映射 tools/custom stream 为安全 activity 事件并实现生命周期持久化策略
- [x] 3.5 采集 Agent 轮次、工具调用、延迟、失败及 provider cache token 指标

## 4. 客户端方案卡片

- [x] 4.1 在前端 reducer 中恢复、合并和去重 assistant 消息附件
- [x] 4.2 实现单卡/多卡、修改版、窄屏和键盘可访问的方案卡片
- [x] 4.3 复用历史方案详情 API 打开卡片，并让移动端安全忽略附件 UI

## 5. 测试与验收

- [x] 5.1 覆盖工具身份注入、越权、冻结记忆、非法转换、明确指令和幂等提交
- [x] 5.2 覆盖偏好/到访语义、南京三日游搜索、版本去重、限量及附件数据库/API/SSE 恢复
- [x] 5.3 覆盖 ReAct 行为、token/activity 流、失败净化、断线重连、缓存前缀与指标
- [x] 5.4 覆盖 Web 卡片交互、窄屏布局和现有 reducer 回归
- [x] 5.5 运行后端、前端与移动端相关测试并修复回归
