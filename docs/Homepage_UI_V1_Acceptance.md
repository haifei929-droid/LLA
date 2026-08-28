# LLA Homepage UI V1 Acceptance Matrix

- **Spec:** `docs/Homepage_UI_V1_Development_Spec.md`（唯一权威来源，2026-08-28）
- **Scope:** 仅首页 UI；不改业务逻辑、API、数据模型或状态机（Spec §10）
- **Evidence types:** DOM 结构（自动）、计算样式（自动）、截图（人工）、隔离库功能回归（自动）、测试基线

## 1. 第二次独立验收问题整改

| 验收问题 | 严重度 | 整改 | 证据 |
|---|---|---|---|
| P0-06 三色只写注释、未应用 | 必须改正 | 蓝灰/青灰/紫灰落到分区色条、标签圆点、深色文字与 hover 表面 | 计算样式：hero `rgb(127,152,170)`、material `rgb(127,169,167)`、mode `rgb(146,142,170)`、label dot `rgb(127,152,170)` |
| P0-09/P0-10 仅入口级验证 | 必须改正 | 隔离临时库端到端回归 FR-01/02/03 | 见 §3 |
| P1-02 无 focus ring | 应当改正 | 全局 `:focus-visible { outline: 2px solid #607b8a; outline-offset: 2px }` | styles.css |
| P1-07 侧栏按钮 42.4px | 应当改正 | `.sidebar-nav button { min-height: 44px }` | 计算样式 `44px` |
| P1-01/P1-06 缺 Loading/Error | 应当改正 | 首页骨架 Loading + 后端不可用错误态（含重试） | App.jsx `loading`/`retry`；`.skeleton-block`/`.notice.error` |
| 截图尺寸不符 | 建议 | 重拍 1440×900 / 1024×768 / 390×844 | `docs/screenshots/home-{desktop,medium,narrow}.png` |

## 2. 交付检查矩阵（Spec §12）

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 三功能分区顺序正确、当前训练权重最高 | ✅ | DOM 顺序；`.training-hero` 大卡 40/48px 内边距 |
| 2 | Quote 完全移除 | ✅ | `.hero`/`.recommendation`/`.grid` 计数 0，无 blockquote |
| 3 | 页面标题弱化 | ✅ | `.page-title` 22px/500，低于 hero 标题 32px/700 |
| 4 | 左侧导航窄 | ✅ | `.sidebar` 208px |
| 5 | 三低饱和冷色实际应用 | ✅ | 计算样式见 §1 |
| 6 | 训练方式卡轻量 | ✅ | 1px 弱边框、无阴影、hover 浅紫灰面 |
| 7 | 状态一致 | ✅ | 默认/hover/active/empty + 骨架 Loading + Error 恢复态 |
| 8 | 三档宽度无溢出 | ✅ | 1440/1024/390 `overflow=False` |
| 9 | 业务逻辑/API 未改 | ✅ | `/api/home/recommendation` 已删；`pytest` 106/106 |
| 10 | 截图证据 | ✅ | 三视口 + 功能回归截图 |

## 3. 隔离库功能回归（P0-09/10 完整结果）

在独立临时数据库 + 8001 端口跑通（不污染主库）：

| 用例 | 结果 | 证据 |
|---|---|---|
| FR-02 无素材空状态 | ✅ | `empty-hero` 存在，文案「还没有开始训练」 |
| FR-03 当前素材显示 | ✅ | 创建后 hero 标题/状态「首次盲听」/素材卡正确 |
| FR-01 继续当前训练（完整） | ✅ | 状态推进「首次盲听→理解检查→听写 Part 1」，点击「继续训练」进入训练页且听写面板渲染 |

截图：`docs/screenshots/home-e2e-{empty,current,training}.png`。

## 4. 仍待独立验收确认

- [ ] FR-04 新素材完整创建/选择（候选素材依赖外网 VOA/BBC，需真实验收）
- [ ] FR-06 不可用训练方式（当前数据无此状态）
- [ ] FR-10 权限/受限请求错误反馈
- [ ] 完整听写/朗读评分流程（后端 106 测试已覆盖，前端面板渲染已验证）
- [ ] 真实触控设备误触检查

## 5. 截图证据清单

| 文件 | 视口 | 用途 |
|---|---|---|
| `home-desktop.png` | 1440×900 | 桌面布局 |
| `home-medium.png` | 1024×768 | 中等宽度 |
| `home-narrow.png` | 390×844 | 窄屏 |
| `home-e2e-empty.png` | 1440×900 | 空状态 |
| `home-e2e-current.png` | 1440×900 | 当前训练/素材 |
| `home-e2e-training.png` | 1440×900 | 继续训练进入听写面板 |
