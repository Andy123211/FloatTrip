# Vue 规划演示客户端

这是 FloatTrip 后端的独立 Vue 3 + TypeScript 演示页面，用于展示规划 API 的真实调用链。它调用项目现有的 FastAPI/Pydantic 登录、Run、Runtime 事件和历史行程接口；规划、LangGraph、POI 查询及 CP-SAT 求解仍由上游后端执行。没有新增 MCP 接入，也不包含模型或高德凭据。

## 启动

先按仓库根目录 README 安装依赖并启动 FastAPI：

```bash
python run.py
```

再启动客户端：

```bash
cd vue-planning-demo
npm install
npm run dev
```

浏览器打开 Vite 显示的本地地址（默认 `http://localhost:5173`）。开发服务器将 `/api` 请求代理到 `http://127.0.0.1:8765`。首次可注册演示账号，也可使用当前服务上的已有账号。规划依赖服务器已配置的模型与高德 Web 服务凭据。

## 请求流程

1. 登录/注册获取 Bearer token。
2. `POST /api/runs` 发送 `RunCreate`，以 Pydantic 的 `RunKind`、`PlanningBriefRepository.required_missing` 和 `TravelPlanState` 约束为准。
3. 轮询 `GET /api/runs/{run_id}/events` 增量读取持久进度事件，并读取 `GET /api/runs/{run_id}` 状态。
4. 成功后请求 `GET /api/history/{itinerary_id}`，显示服务端保存并投影的逐日行程。

客户端类型定义参考 `app/api/runtime_routes.py`、`app/runtime/models.py`、`app/planning/schemas.py` 和移动端生成的 `mobile-app/src/api/schema.ts`。Web 主界面仍为仓库上游的 React；此目录是独立的 Vue + TypeScript 演示客户端，不替换主应用。

## 来源与许可

此工作区基于 `https://github.com/shouzhuoshouzhuo/FloatTrip`。本次只新增独立演示客户端，未复制其他仓库代码。当前检出的上游仓库根目录没有发现 `LICENSE`/`COPYING` 文件；本演示不宣称或补发上游许可，复用或分发上游项目应先确认权利人许可。
