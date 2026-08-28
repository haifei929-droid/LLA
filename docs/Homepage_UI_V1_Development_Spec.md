# LLA Homepage UI V1 Development Specification

- **Version:** V1.0
- **Date:** 2026-08-28
- **Audience:** frontend/backend development and acceptance
- **Scope:** Homepage (首页) UI V1 — information architecture, visual tokens, home recommendation, component specification, and acceptance criteria
- **Design source:** P0 Development Spec V1.0 §26 (UI 信息架构与全局设计原则) and §26.2 (首页推荐优先级), frozen; `docs/development-flow.md` §2; `docs/acceptance-checklist.md` §0/§1/§5
- **Dependencies:** P0 Training Core + existing API contracts (`/api/materials`, `/api/materials/{id}/progress`, `/api/stats`, `/api/weekly-assessments`, `/api/health`), P1/P2 module entry points
- **Non-regression rule:** This specification does not authorize changes to P0/P1/P2 behavior, state transitions, scoring rules, or existing endpoints. It may add read-only endpoints only. No transcript content may be exposed during blind listening or dictation states.

## 1. Product objective

The homepage is the single entry point of the Training Workspace. Within three seconds of opening it, the user must be able to answer three questions (Spec 31.9 / §26):

1. **我在哪** — which workspace this is and whether the training core is ready.
2. **结果怎么样** — accumulated learning hours, this week's status, and material progress at a glance.
3. **下一步做什么** — one clear, prioritized next action (Spec 26.2), executable with one click.

The homepage is a personal language-training instrument panel, not a course or gamified app (Spec 26): color blocks, basic shapes, numbers, and status symbols carry the information; decoration, animation, icons, illustrations, coins, stars, and streak flames are excluded.

## 2. Goals and non-goals

### 2.1 Goals

- Present the three questions above with zero scrolling on a desktop viewport.
- Render the Spec 26.2 recommendation ladder as a single highlighted action card, with priority order enforced deterministically.
- Show weekly status (this week's learning seconds, Weekly Gate outcome if one exists) next to cumulative hours.
- Keep module navigation one click away for Materials, Weekly Test, Candidates (P1), and Dashboard (P2).
- Formalize the visual tokens (typography, module tints, status colors) introduced by the UI beautification pass so every page uses the same language.
- Keep every page title on one line (no wrapping) at all supported widths.
- Keep the existing single-page navigation model (`view` state in `App.jsx`); no router dependency is introduced.

### 2.2 Non-goals

Homepage UI V1 must not:

- Change the P0 material state machine, dictation/reading rules, or any event endpoint.
- Auto-start training without a click; the recommendation card is an action, not an auto-runner.
- Expose transcript or sentence text for materials in blind-listening or dictation states.
- Add gamification, social, cloud sync, multi-user, themes, dark mode, or an icon library.
- Move training content into the homepage; the homepage only summarizes and navigates.
- Add new material or weekly endpoints unless the recommendation contract in §5.3 requires one.

## 3. Design foundation (frozen)

### 3.1 Visual principles (Spec 26, frozen)

1. Visual positioning: personal language training dashboard / Training Workspace.
2. Elements: color blocks, basic shapes, progress bars, waveforms, numbers, status symbols.
3. Minimal decoration/animation/icons; no course feel, check-in feel, gamification, or cartoon style.
4. Every page answers: where am I → what is the result → what is next.
5. Status color semantics are globally consistent (see §3.3).
6. Training pages follow: top context → core workspace → feedback blocks → primary action. (Homepage is not a training page; it follows §4 instead.)

### 3.2 Typography tokens

| Token | Value | Rule |
|---|---|---|
| `--font-stack` | `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", ui-sans-serif, system-ui, sans-serif` | Mixed CN/EN text |
| Base size | 16px, `line-height: 1.6` | Body text |
| `h1` (workspace title) | `clamp(28px, 5vw, 48px)`, weight 800, `white-space: nowrap` | Never wraps; ellipsis on overflow |
| `h2` (hero slogan) | `clamp(34px, 6vw, 66px)`, weight 800, `line-height: .98`, `white-space: nowrap` | May wrap only at ≤600px viewport |
| Module `h2` (page title) | `clamp(30px, 5vw, 52px)`, weight 800, `white-space: nowrap` | Never wraps; ellipsis on overflow |
| `h3` / card titles | 22–26px, `white-space: nowrap` | Never wraps; ellipsis on overflow |
| Numbers | `font-variant-numeric: tabular-nums` | Metrics align vertically |
| `--eyebrow` | 12px, weight 700, `letter-spacing: .14em` | Section labels |

Rule: **titles never wrap**. Where a title is longer than its container (e.g. a material title), the container truncates with an ellipsis; the layout never reflows the heading.

### 3.3 Status color semantics (global, unchanged)

| Meaning | Block | Text |
|---|---|---|
| Done / pass | `#d9f4e4` | `#17633b` |
| In progress / active | `#e8edf2` | `#5b6875` |
| Locked / not started | `#f0f2f4` | `#9aa6b0` |
| Error / fail | `#f2c6c6` | `#7c2020` |
| Near pass / close | `#f5e9a8` | `#6b5b12` |
| Word-form issue | `#d8ccf0` | `#4a2e7c` |
| Spelling issue | `#f5e9a8` | `#6b5b12` |
| Blank (admitted unknown) | `#dfe4e8` | `#4d5a66` |

### 3.4 Module background tints (established by the beautification pass, now normative)

| Module (view) | Tint | Border |
|---|---|---|
| Home hero | linear-gradient `#e5ecf5 → #f2f6fa → #eef2f6` | `#dbe4ee` |
| Materials (素材) | `#eaf2fc` | `#d3e2f3` |
| Weekly (周测 Gate) | `#f1edf9` | `#dfd6f0` |
| Candidates (候选 P1) | `#e7f4f0` | `#cfe7de` |
| P2 Dashboard | `#faf4e6` | `#efe3c9` |
| Training | `#edf7ef` | `#d6eadb` |
| Dark band (recent materials) | `#17202a` | — |

White content cards (`rgba(255,255,255,.78)` on `#eef1f4` page background, 18px radius, `#dce2e7` border) carry interactive content inside tinted modules.

## 4. Homepage information architecture

Top-to-bottom order on the homepage (desktop, no scrolling for the first three blocks):

1. **TopBar** — eyebrow `LANGUAGE TRAINING AGENT · P0`, `h1` "Training Workspace", health status pill (ok: green `训练核心已就绪`; degraded: red; backend down: `后端尚未启动`).
2. **Hero** — eyebrow `当前阶段`, slogan `把每一次练习，变成可恢复的进步`, one-line status sentence summarizing state ("训练状态、素材进度和学习时长由 Training Core 与 SQLite 保存，下一次打开仍能从上次的位置继续。").
3. **Recommendation band** — the single next action from §5, rendered as one highlighted card with CTA. When the recommended action is reinforcement (Weekly Gate failed), the band uses the error-tint (`#f2c6c6`/`#7c2020`) treatment; otherwise it stays on the dark band (`#17202a`) or a `READY`-green accent.
4. **Stats grid** — three or four number cards: 素材数量 (entry to Materials), 累计学习 (hours, from `/api/stats`), 本周学习 (seconds/minutes, from `weekly_learning_seconds`), 周测 Gate (entry to Weekly; shows `已通过`/`未通过`/`未创建`).
5. **Module entry cards** — P1 候选素材 and P2 仪表盘 entries (smaller than stats; may share the grid).
6. **Recent materials** — dark band (`#17202a`), up to 5 rows, in-progress materials ranked before completed ones (same rule as today), each row showing title, id, and current state; row click opens the material training view.

### 4.1 Question mapping

| Question | Block |
|---|---|
| 我在哪 | TopBar + Hero |
| 结果怎么样 | Stats grid + Recent materials |
| 下一步做什么 | Recommendation band |

## 5. Home recommendation (Spec 26.2, formalized)

### 5.1 Priority ladder

Evaluate in order; the first matching rule wins. `R0` means "no recommendation" (all materials complete and nothing else pending).

| Priority | Rule (26.2) | Condition | CTA |
|---|---|---|---|
| R1 | 周测未通过 / 强化未完成 | Latest weekly assessment gate is `FAILED` or state is `REINFORCEMENT_REQUIRED` / reinforcement items remain un-exact | `进入强化训练` (view `weekly`) |
| R2 | 存在未完成听写 | A material is in `DICTATION_PART_*` (resume the exact Part) | `继续听写 Part N` (view `training`) |
| R3 | 听写完成但复听/理解复测未完成 | Material in `SECOND_FULL_LISTEN` or a pending `SECOND_COMPREHENSION_CHECK` | `继续二次复听` (view `training`) |
| R4 | 存在已解锁朗读 | Material in `READING_AVAILABLE` | `继续朗读 Part N` (view `training`) |
| R5 | 三段朗读完成 | Material in `FULL_READING_ASSESSMENT` | `全文朗读验收` (view `training`) |
| R6 | 当前素材完成 | Material in `LISTENING_COMPLETED` or `FULLY_COMPLETED` (most recently completed first) | `获取下一篇素材` (view `training`, calls `POST /api/materials/next`) |
| R7 | 空库 / 全完成 | No materials at all | `导入素材` (view `materials`) |
| R0 | — | All materials fully completed and weekly gate passed | none — render the default band text |

A material in `READY_FIRST_LISTEN`, `FIRST_COMPREHENSION_CHECK`, or `READING_AVAILABLE` is "in progress" for ranking; between R2–R5, choose by the ladder above (a `READY_FIRST_LISTEN`/`FIRST_COMPREHENSION_CHECK` material is the fallback resume target when no DICTATION/SECOND/READING state exists — the CTA becomes `继续盲听/理解检查`). The fallback returns `priority: "R_CONTINUE"` with CTA `继续训练`.

### 5.2 Recommendation contract (backend authority)

New read-only endpoint:

```
GET /api/home/recommendation
```

Response (single object, 200):

| Field | Type | Meaning |
|---|---|---|
| `priority` | `"R1"…"R7"`, `"R_CONTINUE"`, or `null` (R0) | Matched ladder rule |
| `title` | string | One-line recommendation title (CN) |
| `detail` | string | One-line supporting fact (material title, part number, gate score) |
| `cta` | string | Button label (CN) |
| `target_view` | `"training" | "weekly" | "materials" | "candidates" | "p2"` | Frontend view to open |
| `material_id` | string | null | Material to open when `target_view="training"` |
| `week_id` | string | null | Week to open when `target_view="weekly"` |
| `tone` | `"danger" | "default"` | `danger` for R1 (reinforcement), otherwise `default` |

Deterministic, read-only, no side effects. Implemented in a new `HomeRecommendationService` reading `training_progress`/`materials`/`weekly_assessments` through existing stores. The frontend renders the card from this payload; it must also degrade gracefully (render the default band text) when the endpoint is unavailable or returns `priority: null`.

### 5.3 Data sources (existing contracts, unchanged)

| Need | Source |
|---|---|
| Material list + states | `GET /api/materials` (rows: `material_id`, `title`, `current_state`, `duration_seconds`, `speech_rate_wpm`, `status`); the recommendation service reads `dictation_part_status` / `reading_part_status` / `prepare_status` directly from `training_progress`/`materials` through the database |
| Material detail | `GET /api/materials/{id}` |
| Cumulative + weekly seconds | `GET /api/stats` (`total_learning_seconds`, `weekly_learning_seconds`) |
| Weekly Gate state | `GET /api/weekly-assessments` (latest `week_id`, gate result, `state`) |
| Health | `GET /api/health` |

## 6. Component specification

### 6.1 TopBar

- Left: eyebrow + `h1` (no wrap, ellipsis).
- Right: health pill (nowrap, `flex-shrink: 0`, tabular nums).
- ≤720px: stacks vertically (existing rule).

### 6.2 Hero

- Tinted gradient block (3.4), 24px radius, `padding: 30px 34px 34px`.
- Slogan never wraps above 600px; between 601–720px font shrinks via `clamp(24px, 5.8vw, 40px)`.

### 6.3 Recommendation band

- Placed directly under the hero, above the stats grid.
- `tone=danger`: block `#f2c6c6` / text `#7c2020`, CTA `primary` on error tint.
- `tone=default`: dark band `#17202a` with green accent CTA (`#8ee1b0`), matching the existing recent-materials band language.
- Contains: eyebrow `下一步`, title (no wrap), detail (muted, one line), CTA button.
- Clicking the CTA switches view per `target_view` and opens `material_id`/`week_id` when present.

### 6.4 Stats grid

- Up to 4 cards (`card metric`), each: label, big number (tabular, weight 800), one-line caption.
- 累计学习 shows hours (`total_learning_seconds / 3600`, floor); 本周学习 shows minutes; 素材数量 shows count and acts as Materials entry; 周测 Gate card shows the latest gate outcome and acts as Weekly entry.

### 6.5 Module entry cards

- P1 候选素材 → `candidates`; P2 仪表盘 → `p2`. Same `card action-card` style as today.

### 6.6 Recent materials (dark band)

- `#17202a` band, `section-heading` (title `最近素材` + sync hint), rows up to 5.
- Ordering: in-progress (`current_state` not in `FULLY_COMPLETED`/`LISTENING_COMPLETED`) before completed; stable by `material_id` within groups.
- Row: title (strong, nowrap/ellipsis), `material_id` (muted), state label (green `#8ee1b0`).
- Empty state: `导入预置素材后，这里会显示当前 Part 和恢复位置。`

### 6.7 Empty and degraded states

| Condition | Rendering |
|---|---|
| Backend down | Health pill `后端尚未启动`; API failures surface as `notice` blocks; page never white-screens |
| No materials | Stats show 0; recent-materials band shows empty text; recommendation R7 |
| Recommendation endpoint error | Band shows default text `训练核心已就绪，选择下方模块开始` with no CTA |

## 7. Responsive and behavior rules

- ≤720px: stats grid and module entries collapse to one column; module padding `22px 18px 28px`; `h1` 26px; module `h2` 24px.
- Titles never wrap at any width (see 3.2); overflow truncates with ellipsis.
- Page refresh restores the exact training position (existing behavior — recommendation and progress must agree with `/api/materials`).
- No auto-play, no auto-navigation, no polling loops on the homepage (single fetch per endpoint on mount).

## 8. Acceptance criteria

Automated where marked `[API]` (black-box tests against endpoints), manual otherwise `[UI]`.

| ID | Criterion | Source |
|---|---|---|
| A1 | Opening the homepage shows the three questions (where am I / result / next step) without scrolling on a 1280px viewport. | 31.9 |
| A2 | No course/gamified elements (cards, coins, stars, streak flames, illustrations, animations) appear anywhere. | 26 |
| A3 | `GET /api/home/recommendation` returns `R1` when the latest weekly assessment is failed or reinforcement is incomplete. | 26.2 |
| A4 | Returns `R2` (exact Part number) when a material is in `DICTATION_PART_*` and no higher-priority rule matches. | 26.2 |
| A5 | Returns `R3` for `SECOND_FULL_LISTEN` / pending second comprehension; `R4` for `READING_AVAILABLE` (Part number); `R5` for `FULL_READING_ASSESSMENT`. | 26.2 |
| A6 | Returns `R6` (next-material CTA) when the most recent material is `LISTENING_COMPLETED` or `FULLY_COMPLETED`. | 26.2 |
| A7 | Returns `R7` for an empty library; `priority: null` when everything is complete and gate passed. | 26.2 |
| A8 | Priority strictly follows the ladder: with a failed gate and a dictation in progress, `R1` wins. | 26.2 |
| A9 | Recommendation endpoint is read-only: repeated calls never change material or weekly state. | §5.2 |
| A10 | Recent-materials rows rank in-progress before completed; clicking a row opens training at that material. | checklist §1 |
| A11 | Cumulative minutes increase after training (compared against time logs). | checklist §1 |
| A12 | Every page/module title renders on one line at 320px–1440px (ellipsis only as overflow). | user request |
| A13 | Each module view shows its distinct tint (3.4); status colors match 3.3 globally. | user request |
| A14 | Weekly Gate card reflects the latest gate outcome and opens the weekly view. | checklist §1 |
| A15 | ≤720px: single-column layout, no horizontal overflow, no broken blocks. | checklist §5 |
| A16 | Refreshing the page restores position; recommendation agrees with `/api/materials` states. | checklist §4 |
| A17 | Backend-down or recommendation-endpoint failure shows clear messaging, never a white screen. | checklist §4 |

## 9. Implementation notes

- Files: `frontend/src/App.jsx` (home view markup: recommendation band + stats grid + entries), `frontend/src/styles.css` (tokens in §3), new `backend/app/core/home_recommendation.py` + route `GET /api/home/recommendation`, tests in `backend/tests/`.
- Frontend changes require `npm run build` (FastAPI serves `frontend/dist`); verify at `http://127.0.0.1:8000`.
- Test suite must stay green (current baseline 106 tests) — the new endpoint adds tests only; no existing test may change.
- Acceptance environment per `docs/acceptance-checklist.md`.

## 10. Out of scope for V1

- User-configurable themes, dark mode, font-size settings.
- Homepage widgets/plugins, drag-and-drop arrangement.
- Multi-language homepage copy.
- Real-time updates (WebSocket) or push notifications.
- Any change to P0/P1/P2 training rules or existing endpoints.
