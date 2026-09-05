# FloatTrip 小红书推广素材

本目录包含 6 张 1080×1440 小红书轮播图，以及一条 24 秒、6 场景的 HyperFrames 兼容 HTML/GSAP 时间线。

## 输出文件

- `output/01-cover.png` — 封面
- `output/02-why-multi-agent.png` — 为什么需要 Multi-Agent
- `output/03-agent-orchestration.png` — Planner / Reviewer / Time Check 编排
- `output/04-personal-memory.png` — 可控旅行记忆
- `output/05-editable-itinerary.png` — 地图与可编辑行程
- `output/06-open-source.png` — GitHub 开源 CTA

## 重新导出

工程依赖本机 Google Chrome 和 Playwright。使用 Codex 工作区运行时：

```bash
NODE_PATH=/Users/chj/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules \
node scripts/export-stills.cjs
```

本地布局、时间线和二维码检查：

```bash
NODE_PATH=/Users/chj/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules \
node scripts/inspect-layout.cjs

node scripts/audit-timeline.cjs

SWIFT_MODULECACHE_PATH=/private/tmp/floattrip-swift-cache \
CLANG_MODULE_CACHE_PATH=/private/tmp/floattrip-swift-cache \
swift scripts/verify-qr.swift output/06-open-source.png
```

## HyperFrames 说明

`index.html` 使用标准的 `data-composition-id`、尺寸、时长和同步 paused GSAP timeline，可在 HyperFrames CLI 可用时继续进行视频预览和渲染。当前机器上的插件包缺少 CLI 运行依赖，因此静态 PNG 使用本地 Chrome 英雄帧导出器生成；动画结构仍保留在同一份 HTML 中。

品牌颜色、字体、运动和禁用模式见 `DESIGN.md`。
