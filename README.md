# Language Training Agent

> **状态：`p0-accepted`**（2026-08-26，P0 ACCEPTED WITH KNOWN LIMITATIONS，详见 [P0_ACCEPTANCE_REPORT.md](P0_ACCEPTANCE_REPORT.md)）

P0 本地优先的语言训练工作区。当前已具备素材预处理、可恢复训练状态机、听写提交校验，以及盲听/理解检查/Part 完成/二次复听/朗读骨架 API。

## 技术边界

- 前端：React + Vite
- 后端：FastAPI
- 数据库：SQLite
- 文件：本地素材、录音和预处理产物
- AI 能力：通过 adapters 接入，不由 Training Core 直接绑定供应商

## 本地启动

1. 创建虚拟环境并安装后端依赖：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. 启动后端：

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload
   ```

3. 启动前端（依赖安装后）：

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

## 当前阶段验收

- 后端 `/api/health` 返回 `{"status":"ok"}`。
- SQLite 数据库可以初始化，并为旧听写表补齐播放次数和 Memory 目标字段。
- 听写只能在已解锁的 Part 中按句子顺序提交；Part 未完成不能推进。
- 盲听、理解检查、Part 完成、二次复听和朗读骨架接口可驱动主状态机。
- “声音训练素材”入口支持本地素材搜索、详情进入、文本导入和音频播放；当前搜索范围是本地已导入素材。
- 学习时长日志会汇总到 session、weekly 和 total 统计；周测按听写与朗读必测项决定通过或强化。
- 朗读全篇接口已支持语速、停顿、重音和时长结果字段，具体 ASR/朗读分析仍通过 adapter 接入。
- 听写上下文接口不返回句子文本（盲听边界在服务端），Reveal 后才返回原文；Reveal 不降低逐字正确通过标准。
- 前端可完成 L0 听力闭环：首页（进行中素材优先）/素材搜索导入/训练页（盲听→理解检查→逐句听写：播放计数、占位、提示、Reveal、Diff 色块→二次复听可看原文）。
- 朗读评分闭环：本地确定性音频分析（时长/停顿/RMS 能量起伏，重音为简化代理待校准）→ Rule Engine 三维独立 PASS/CLOSE/FAIL（阈值经 Settings 参数化）→ 评分落库后才可完成 Part / 全文验收；前端支持浏览器录音（WAV 编码）与段尾三维反馈。
- 学习时长按周窗口聚合（LTA_WEEKLY_WINDOW：calendar 自然周 / rolling7 近 7 天，时间可注入测试），activity_type 限定 Spec 13.1 活动集合。
- 后端测试（62 个，含 acceptance 黑盒、并发、跨天恢复、朗读评分、周窗口、周测闭环、异常处理）和前端生产构建均通过。
- 验收复核（2026 第二轮）：真实素材 E2E（BBC 人声素材全链路至 FULLY_COMPLETED）、真实人声朗读评分（慢读 FAIL / 匹配朗读三维 PASS，pause 阈值经真实语音校准：最小停顿 500ms + 比例容差）、Targeted Retest 显式状态流（强化全对 → TARGETED_RETEST → 确认 → GATE_PASS）、盲听完成需播放到结尾（前端锁定）、强化闭环前端修复。
- **P1-1（候选素材 + 难度升级）**：候选管线（`MaterialCandidateService`）——VOA 慢速为初始 Provider，15–20 分钟硬时长区间，音频质量三档分级（Clear/Acceptable/Poor，`AudioQualityAnalyzer` 带指标/版本审计），Transcript 完整性校验（`TranscriptValidator`），最多 3 候选按质量/语速/时长排序，Poor 与缺失字段永不进入候选；候选仅以 `material_candidates` 存在，用户选择后 `prepare`（幂等键、失败可恢复）才创建正式 Material（`source_candidate_id`/`speed_stage`/`prepare_status` 关联）。难度升级（`DifficultyProgressionService`）——读 P0 周测生成幂等 `WeeklyGateRecord`，连续 8 训练周 PASS（听写 ≥80、朗读通过、无强化、周间隔容差）才 `upgrade_eligible`；提示-确认流程（UPGRADE_CONFIRMED/KEEP_CURRENT/DECIDE_LATER），KEEP/DECIDE 进入 4 周冷却，确认后仅推进一个 `speed_stage`（STAGE_1→2→3，时长等变量不变），Stage 3 封顶。P0 训练核心零改动。API：`/api/p1/material-candidates/search|prepare`、`/api/p1/difficulty/weekly-gate|profile|prompt|upgrade-decision`。
- 预置素材：`preset-002`《The Story of Rain》——166 句原创英文、15.6 分钟慢速清晰语音（edge-tts 逐句合成 + 精确句级时间戳），按 Spec 3.1 的 15–20 分钟设定生成；三段切分优先自然语义段落边界（`natural_part_boundaries`，Spec 3.2）；生成脚本 `backend/scripts/generate_preset_material2.py` 可复现。
- 自动搜索素材：素材完成后（训练页「获取下一篇」或 `POST /api/materials/next`）按难度规则自动搜索并导入下一篇。**源优先级：VOA Learning English 慢速英语（标准，公版）→ BBC Learning English 6 Minute English（兜底）**；搜索条件含**音质门槛**（采样率/SNR/静音比例/时长，阈值经 Settings 配置）。管线：下载 → ffmpeg 转码 → 本地 ASR（faster-whisper）生成段级时间戳 → 文稿句子按词序列锚点对齐（未匹配句在锚点间按词数插值，时间戳单调且在音频范围内）→ 三段切分 → 发布。VOA 官方文稿由客户端 JS 加载无法服务端获取，其文稿由更高精度 ASR（small 模型）生成并标注；BBC 使用官方文稿。
- **素材可跳过**：训练页「跳过此素材，换一篇」——素材标记 SKIPPED 并从后续搜索排除，自动搜索替代素材（对应 Spec 12 训练节奏控制）。
- 难度规则（Spec 3.1/16）：档位 = 时长（short/standard/long）× 语速（slow/medium/fast）；**一次只升级一个变量**（先时长后语速）；升级由周测连续稳定通过触发（`MaterialRecommender.stable_pass_rounds`，默认 2 次），累计时长不触发；`upgrade_available` 随搜索返回。
