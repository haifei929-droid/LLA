# LLA P0 开发流程（最小闭环优先 + 逐步验证）

> 依据：P0 Development Spec V1.0（冻结版）；基线：两轮代码评审结论（2026-08-26）。
> 目标：每个里程碑是一个「后端 API + 自动化测试 + 前端页面」的垂直切片，可独立演示、可独立回退、可独立验收。

## 1. 现状基线（评审确认的事实）

| 模块 | 状态 |
|---|---|
| 后端训练主状态机 + 事件 API | ✅ 完整跑通（创建→盲听→理解→听写→复听→朗读→全文验收→FULLY_COMPLETED） |
| 听写规则（逐字判定/错误分类/Listening Memory/listen_count/顺序守卫） | ✅ 完整，实测守卫全部生效 |
| 朗读 | ⚠️ 仅骨架：`reading_attempts` 只记 overall_pass，三维评分未实现 |
| 周测 / 强化 / 复测 | ❌ 仅表和枚举，无服务无 API |
| 学习时长 TimeLog→Stats | ❌ 仅表，无读写代码 |
| 前端 | ❌ 占位页，无页面 |
| 测试 | ✅ 16 个通过（单测 + API 集成 + 迁移） |
| 已知缺陷 | 重复素材创建返回 500；dictation 的 progress 更新无乐观锁 |

## 2. 设计原则

1. **闭环优先**：先打通最小合法闭环（L0 听力路径，Spec 11/20 允许跳过朗读），再逐层叠加朗读（L1）、周测（L2）。
2. **待校准项不阻塞**：朗读评分阈值（Spec 33）参数化 + 确定性模拟分析器先行，真实 ASR 通过 adapter 后置替换。
3. **验收用例先写**：Spec 31 每条验收映射为 `tests/acceptance/` 下的黑盒测试（只走 API），先红后绿。
4. **测试友好三件套**：clock 注入（时间可快进）、adapter 替身（无真实 ASR/LLM 也跑闭环）、合成素材（wave 音频 + 时间戳，无需真实素材）。
5. **红线**：
   - 任何里程碑不得让已有测试变红（规则变更须同步改测试）；
   - 盲听阶段不得新增暴露 transcript 的端点（Diff 只回传 errors，不回传全文）；
   - 状态变更必须可追踪（乐观锁）、可恢复（跨天恢复测试）。

## 3. 闭环分层

| 层 | 闭环内容 | 验收 | 前置 |
|---|---|---|---|
| L0 听力闭环 | 素材→盲听→理解→听写 P1/2/3→复听→理解→Listening Completed | 31.1(听力)/31.2/31.3/31.4 | 后端已就绪 |
| L1 朗读闭环 | +朗读训练→三维评分→全文验收→Fully Completed | 31.1(全)/31.5 | L0 |
| L2 周测闭环 | +听写周测→Gate→强化→针对性复测 | 31.6/31.7 | L1 + 时长 |
| 横切 | 学习时长 TimeLog→Stats | 31.8 | 独立 |

## 4. 里程碑（M0–M5，预估 10 工作日）

### M0 基线加固 + 测试基建（0.5 天）✅ 完成
- 修复：重复 material_id 创建 500 → 409（IntegrityError 捕获）
- 修复：dictation 提交的 progress 更新补 version 乐观锁（冲突 → TransitionError → 409）
- 状态码语义锁定：422（Pydantic 参数）/ 400（业务前置条件如 Part 未完成）/ 409（状态、顺序、进度冲突）/ 404
- 测试基建：
  - `tests/fixtures.py`：MaterialFactory（任意句数素材）+ 合成音频工具（正弦波 + 静音段，供 M3 朗读评分断言）
  - `tests/acceptance/`：黑盒验收测试目录（只走 API）
- 验收：pytest 全绿（16 → 23）

### M1 L0 听力闭环端到端可用（2 天）✅ 完成
- 后端：dictation-context 端点（无文本 + exact 标记 + 状态守卫）；submit 响应 revealed 时返回 expected_text
- 前端（Spec 26 信息架构，功能只点亮 L0）：
  - 首页：继续上次训练（progress 驱动推荐）+ 素材概览 + 累计小时
  - 素材进度页：Part 1/2/3 听写矩阵 + 流程状态
  - 听写页：播放 → 输入/`____`/提示/Reveal → Diff 色块 → 上报 listen_count
  - 盲听页 / 理解检查页 / 二次复听页（可看原文）
- 验收：`acceptance/test_p0_flow.py` + `test_dictation_context.py` + 手动走通

### M2 学习时长统计（1 天，可与 M1 并行）✅ 完成
- 后端：`POST /time-logs`（activity_type 枚举 + active_seconds）；周窗口聚合（calendar/rolling7 可配置，clock 注入）；TimeLog→LearningStats
- 前端：首页累计小时
- 验收：`test_weekly_window.py`（跨周边界、聚合正确性、非法 activity 拒绝）→ 31.8

### M3 朗读闭环 L1（2–3 天）✅ 完成
- 后端：`adapters/audio.py`（AudioAnalyzer 确定性实现：时长/VAD 停顿/RMS 能量起伏，重音维为简化代理并标注待校准）；`core/reading_scoring.py`（RuleEngine：三维分别 PASS/CLOSE/FAIL，阈值经 Settings 参数化）；评分 API（PART/FULL 复用 `reading_attempts`，三维落库）
- 规则变更（连带更新测试）：`complete_reading_part` 与全文验收要求对应三维 PASS 评分记录
- 前端：朗读页（大文本 + 原音 + 浏览器 WAV 录音 + 段尾三维反馈 + 全文验收）
- 验收：`test_reading_scoring.py`（合成音频构造过快/过慢/停顿错误 → 确定性断言 + 重复评估稳定性）→ 31.5
- 边界：真实 SenseVoice/FunASR 不接入不阻塞；阈值在验收样本阶段校准（Spec 33）

### M4 周测闭环 L2（2–3 天）✅ 完成
- 后端：`core/weekly.py`——测试类型自动判定（Spec 14.1：按本周 TimeLog 活动推断，无朗读训练则只听写）；80% 听写门槛服务端化（Spec 14.2：低于阈值强制 Gate 失败，与调用方上报无关）；听写测试项生成（从素材池按 week_id 确定性抽取，词汇不越界）；测试完成自动评分（exact 比例）；强化包（Spec 15.1：周测未 exact 项 + 本周训练错误词 + Listening Memory 弱点 → 素材句匹配，上限配置化）；全部强化项 exact 后 Gate 恢复 WEEKLY_GATE_PASS（Spec 15.3）；周窗口函数提取到 `core/time_window.py` 与时长共用
- 新表 `weekly_test_items`（TEST/REINFORCEMENT 两类项，is_exact/attempt_count）
- 前端：周测页（创建/生成测试/逐句听写/自动评分/Gate 状态/强化包/朗读测试复用 ReadingPanel 评分后 record_reading）+ 首页周测卡片
- 验收：`test_weekly_loop.py`（自动判定/80% 门槛/自动评分/强化闭环恢复 Gate/API 往返）→ 31.6/31.7

### M5 异常处理与综合验收（1–2 天）✅ 完成
- 31.10 落地：朗读分析失败 → 不落库（绝不误标 PASS）+ 录音文件保留可重试（`test_exceptions.py`）；未停止的 TimeLog 不计入统计；素材搜索网络/ASR 失败 → 降级下一 Provider 且不触碰既有训练状态，全失败返回 409 而非 500
- 补端点：`GET /weekly-assessments/{week_id}`（前端周测刷新此前隐式依赖缺失端点）
- 综合验收：`acceptance/test_p0_acceptance.py`——黑盒单脚本跑通「素材全链路至 FULLY_COMPLETED + 周测 FAIL→强化→复测→PASS + 重启恢复（Part/句/尝试）+ 时长聚合」
- 前端手动验收清单 `docs/acceptance-checklist.md`（首页 3 秒、色块语义、Diff 颜色、朗读无逐词红绿、异常恢复）

### 追加里程碑（用户需求变更）：自动搜索素材（原 Spec 30 Non-goal → 已纳入 ✅ 完成）
- 素材源：`BBCLearningEnglishProvider`（BBC 6 Minute English：官方文稿 + MP3 直链，服务端渲染；版权属 BBC，个人学习用途，来源记录于素材行）
- 时间戳（Spec 24.1 完整落地）：本地 ASR `WhisperASRProvider`（faster-whisper base，CPU int8）→ 官方文稿句子按词序列锚点对齐（统一标点清洗；未匹配句在相邻锚点间按词数插值；结果单调且在音频范围内）
- 难度规则 `core/material_recommender.py`：档位（时长 short/standard/long × 语速 slow/medium/fast），一次只升级一个变量，周测连续稳定（默认 2 次）触发升级，累计时长不触发
- API：`POST /api/materials/next`（搜索→转码→ASR→对齐→三段切分→发布；返回 source_url/source_name/upgrade_available/criteria）；前端素材完成态「获取下一篇」
- 验证：`test_material_search.py`（规则升级/对齐/管线，fake provider）+ 真实验收（37s 全链路，107 句对齐零乱序零溢出）

### P1-1（P1 Development Spec V1.0）：候选素材 + 难度升级 ✅ 完成
- 候选管线：VOA 慢速初始 Provider；15–20 分钟硬时长区间（RSS `itunes:duration` 预筛 + 实际音频复核）；音质三档分级（Clear/Acceptable/Poor，`AudioQualityAnalyzer` 输出 SNR/响度/clipping/指纹/版本审计，`AudioQualityReport` 落库）；Transcript 完整性校验（`TranscriptValidator`：覆盖率/句数/词数）；最多 3 候选按 Clear 优先、时长居中、语速排序；Poor/缺失字段永不进入；候选去重（指纹）、批次、淘汰统计（rejection_summary）、有效期
- 选择与准备：`MaterialPreparationService.prepare`（幂等键；重复请求返回同一 Material；失败保留候选可恢复 + failure_code；成功才创建 `READY` Material 并关联 `source_candidate_id`/`speed_stage`）
- 难度升级：`DifficultyProgressionService`——读 P0 周测（不修改 P0）生成幂等 `WeeklyGateRecord`；连续 8 训练周 PASS（听写≥80、朗读通过、无强化、周间隔容差 10 天）→ `upgrade_eligible`；提示（`upgrade_prompts`）→ 用户决定（UPGRADE_CONFIRMED 推进一个 stage 并重置计数 / KEEP_CURRENT、DECIDE_LATER 进入 28 天冷却）；STAGE_3 封顶；`profile_version` 随升级递增
- 真实验收发现并修正：VOA 慢速 15–20 分钟素材位于 feed 深处（199 条中 5 条），PROCESS_LIMIT 6→50 后真实搜索可命中；真实难度流程（8 周→资格→幂等→错误码）运行验证通过
- 测试：`test_p1_candidates.py`（筛选/排序/去重/幂等/过期/API）+ `test_p1_difficulty.py`（连续/归零/冷却/升级/封顶/幂等/API），全量 77 通过，P0 测试零回归

## 5. 依赖图与并行策略

```
M0 ──┬──→ M1 ──→ M3 ──→ M4 ──→ M5
     └──→ M2（独立，可并行）
     P1（候选素材 + 难度升级）在 M5 后作为独立里程碑 ✅ 完成
```

## 6. 验收映射表（Spec 31 → 里程碑 → 测试文件）

| 验收项 | 里程碑 | 测试文件 |
|---|---|---|
| 31.1 主链路 | M1/M3 | `test_l0_listening_loop.py` + `test_l1_reading_loop.py` |
| 31.2 听写 | M1 | 既有 dictation 测试 + acceptance 补充 |
| 31.3 跨天恢复 | M1 | `test_resume.py` |
| 31.4 Listening Memory | M1 | `test_listening_memory.py`（跨素材/4 遍/hint/reveal 分布） |
| 31.5 朗读评分 | M3 | `test_reading_scoring.py`（合成音频） |
| 31.6 周测 Gate | M4 | `test_weekly.py` |
| 31.7 强化/复测 | M4 | `test_reinforcement.py` |
| 31.8 学习时长 | M2 | `test_time_logs.py` |
| 31.9 UI 效率 | M1+M5 | 手动清单 |
| 31.10 异常 | M5 | `test_exceptions.py` |

## 7. 风险与预案

| 风险 | 预案 |
|---|---|
| 重音维度无真实声学模型 | RMS 简化代理 + 待校准标注；adapter 可替换；验收样本校准（Spec 33 允许） |
| 周测生成句生硬 | 规则模板兜底 + LLM 可选增强；词汇范围校验为硬约束 |
| 前端工作量低估 | M1 只做 3 页且复用组件；周测/强化页复用听写/朗读组件 |
| 真实素材缺失 | 合成素材 fixture 先行；真实清单属待校准，不阻塞 |
| 盲听文本污染 | Diff 只走响应不回传全文；M1 测试锁定「盲听阶段无文本端点」 |
| M3 规则变更破坏 M1 测试 | 变更随里程碑同步改测试（红线） |

## 8. 执行纪律

- 每里程碑结束时跑全量 pytest，必须全绿才进入下一里程碑
- 里程碑超过 3 天未转绿 → 回退拆分步骤
- 手动验收超过 1 天 → 砍功能保闭环
- 任何状态变更必须走 API（黑盒），测试不直调 store
