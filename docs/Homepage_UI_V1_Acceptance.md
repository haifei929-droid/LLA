# LLA Homepage UI V1 Acceptance Matrix

- **Spec:** `docs/Homepage_UI_V1_Development_Spec.md`（唯一权威来源，2026-08-28）
- **Scope:** 仅首页 UI；不改业务逻辑、API、数据模型或状态机（Spec §10）
- **Evidence types:** DOM 结构（自动）、截图（人工）、代码 diff、测试基线

## 1. 交付检查矩阵（Spec §12）

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 三个功能分区顺序正确，当前训练视觉权重最高 | ✅ | DOM：`.section-label` 顺序 = 当前训练 → 当前素材/新素材 → 训练方式；`.training-hero` 为唯一大卡片（40/48px 内边距、24px 圆角、白底） |
| 2 | Quote 及占位空间完全移除 | ✅ | DOM：`.hero` / `.recommendation` / `.grid` 计数 = 0 |
| 3 | 页面标题已弱化 | ✅ | `.page-title` 22px / weight 500，低于当前训练标题（clamp 26–32px / 700） |
| 4 | 左侧导航窄 | ✅ | `.sidebar` width 208px（192–224px 范围内），导航 5 项 |
| 5 | 三低饱和冷色、明度接近、无对立色 | ✅ | `#7f98aa`（当前训练蓝灰）/ `#7fa9a7`（素材青灰）/ `#928eaa`（训练方式紫灰）；页底 `#f4f6f8` |
| 6 | 训练方式卡轻量 | ✅ | `.mode-card` 弱边框 1px `#e0e6ea`、无阴影、hover 仅换底 |
| 7 | 空/加载/错误/选中/禁用状态一致 | ⚠️ 首页部分 | 首页空态 `empty-hero` / 选中 `sidebar .active` 已统一；训练页沿用已验收状态色（§10 不重构） |
| 8 | 桌面/中等/窄屏无溢出 | ✅ | Playwright 截图三档（见 §2） |
| 9 | 业务逻辑/API/数据/埋点未改 | ✅ | 后端推荐端点已回退；`pytest` 106/106 基线不变 |
| 10 | 提供实现截图 | ✅ | `docs/screenshots/home-{desktop,medium,narrow}.png` |

## 2. 截图证据（人工验收）

| 视口 | 文件 | 尺寸 |
|---|---|---|
| 桌面 | `docs/screenshots/home-desktop.png` | 1280×800 |
| 中等 | `docs/screenshots/home-medium.png` | 900×800 |
| 窄屏 | `docs/screenshots/home-narrow.png` | 390×844 |

## 3. 本次纠正（相对前次偏离）

| 项 | 前次（已回退） | 本次 |
|---|---|---|
| 首页结构 | 推荐梯 + 统计网格 | 三段式：当前训练 → 当前素材/新素材 → 训练方式 |
| Quote | 保留 hero 大字标语 | 移除 |
| 顶部标题 | 48px Hero 标题 | 22px 弱标题 |
| 侧栏 | 无 | 208px 窄侧栏 |
| 色系 | 绿/紫/青/米杂色 | 三低饱和冷色 + 冷灰底 |
| 后端 | 新增 `/api/home/recommendation` | 回退，纯前端，复用现有端点 |

## 4. 待人工确认

- [ ] 三档截图视觉复核（冷色、留白、层级、无溢出）
- [ ] 窄屏触控目标 ≥ 44px
- [ ] 键盘焦点可见（focus ring 2px）
