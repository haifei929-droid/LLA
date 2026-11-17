# LLA 素材来源治理：DSH 实施交接计划

日期：2026-11-17。工作目录固定为 `D:\codex\LLA`。

## Gate 与授权

- 调查代码基线：`09be6d97e78adef9509bbe0a2163899e146d7b85`。
- 流程基线提交：`923cbab`，仅纳入原有 `CODEX_ENGINEERING_WORKFLOW_V2 .md`，内容未改。
- 用户已确认：允许平台原始完整短课，取消候选整篇 15–20 分钟硬门槛；保留三段训练与状态机；提交流程基线、交由 DSH 实现。
- 本计划 READY 仅指治理规则开发及离线小规模验证；不代表任何平台已获授权或已激活。
- DSH 开发，Codex 独立 Review / Verify。正式验收绑定 DSH 交付的完整 commit SHA，开始前核对 root、HEAD、clean status。

PLAN STATUS: READY FOR IMPLEMENTATION

MINIMALITY CHECK: PASS

## 来源评估（2026-11-17）

| 优先级 | 来源 | 已有证据 | 未决条件与策略 |
|---|---|---|---|
| 1 | 50languages / book2 English UK | 官方区分 UK/US，100 课，产品定位 A1–A2；音频 CC BY-NC-ND 3.0 US，部分语言混合男女声 | 第一候选，非已批准主源。文字版权另行保留；逐课 CEFR 映射、稿音一致性、说话者及下载稳定性待验证；不能假造 B1 |
| 2 | British Council LearnEnglish | A1–B1 分类、英文稿；允许有条件个人非商业使用 | 逐课程确认口音，不能推定全站英音；禁止修改、重新发布等条件需核对 LLA 用法 |
| 3 | Breaking News English | 英音/北美音版本，分速音频与文字；Level 0 为 A2 | 版权明确限制 App/LMS 复制，其他 MP3 使用需书面许可；未获许可不得接入；真人音源证据待核实 |
| 4 | BBC Learning English | 仓库已有 6 Minute English 适配器 | 仅后续补充；当前许可页访问失败，逐项授权、口音与难度待确认 |

官方依据：
- https://50languages.com/licence
- https://50languages.com/language-mp3
- https://play.google.com/store/apps/details?id=com.goethe.f50languages
- https://www.britishcouncil.org/terms
- https://learnenglish.britishcouncil.org/free-resources/listening/a1/booking-table
- https://learnenglish.britishcouncil.org/free-resources/general/audio-zone
- https://breakingnewsenglish.com/copyright.html
- https://breakingnewsenglish.com/news_levels.html
- https://breakingnewsenglish.com/about.html

50languages 直接访问返回 403，证据来自官方页面搜索索引；不得绕过限制。所有来源均尚未做音频实测。免费访问、发现目录、合法下载、允许在 LLA 使用是不同判断。音频许可不替代文字稿许可。不发送授权询问信件，除非用户另行要求。

## 当前事实及边界

Confirmed Issue：
- `main.py` 注册 VOA→BBC 回退；P1 `material_candidates.py` 默认 VOA，ASR 结果直接作为文字稿；无口音、授权、课程队列约束。
- 候选按音质、时长及语速排序，失败主要为批次计数；没有持久平台完成证据。
- `transcript_validator.py` 以末句时间/总时长衡量覆盖，不能证明稿音对应或逐字正确。
- `material_preparation.py` 创建素材和来源关联分两次事务；旧入口和手工入库需防绕过。
- `materials.py` 按创建时间倒序，前端取第一项；周测从全库 sentences 抽样。
- 旧 `material_recommender.py` 使用两次周测规则，不能继续控制新选材升级；复用现有 P1 八周确认机制。
- 预置脚本使用 en-US-JennyNeural TTS。不得把历史 PRESET 或 BBC 名称当作英音证据。
- 当前主库只读统计：materials 7、candidates 1、progress 7、dictation_attempts 138、weekly_assessments 8。来源标签 BBC 4、VOA 1、空 2。这些是调查快照，迁移验证应以当时副本为准。

Inferred Risk：音质统计不能可靠识别音乐、口音；来源文字可能受独立许可；非原子准备及半文件缓存存在崩溃恢复风险，需故障注入验证，不能宣称已经发生数据损坏。

Out of Scope：训练状态机、Backend Transition Authority、operation_id 语义、Part 完成规则、听写判定、朗读评分、周测评分/强化流程、跨天进度、累计时长及八周升级规则的重写；新爬虫框架、新数据库、后台系统、全站批量抓取、TTS 替代、无关修复。

## 最小方案与预计文件

1. `backend/app/db/schema.sql`、`connection.py`：复用 SQLite、迁移机制、material_candidates、audio_quality_reports、search_audits、app_metadata。扩展候选表作为持久发现清单，未下载信息可为空，旧字段保留兼容；app_metadata 保存唯一当前来源周期、目录版本/范围、发现游标、报告及确认记录。不另建数据库。
2. `core/material_candidates.py`：复用服务及重试，将发现、审核、下载、入库状态持久化；取消 VOA 默认；按固定目录顺序处理，不跨平台回退。
3. `core/material_search.py`、`main.py`：旧 next 入口统一调用治理后的流程，不保留平行来源选择或两周升级旁路。
4. `core/material_preparation.py`、`materials.py`、`transcript_validator.py`：复用预处理、逐句对齐、质量分析。入口与正式入库均校验治理条件；幂等重放先返回原结果，不受过期或后续换源破坏。素材、句子、初始进度、来源关联、入库状态须同事务成功。
5. `api/routes.py`、`api/schemas.py`、`frontend/src/App.jsx`、`CandidatePanel.jsx`：沿用既有页面，展示待审/隔离/失败与顺序；取消 15–20 默认限制；暴露最小报告与明确确认操作。新任务只允许当前平台的下一合格课程项，不能通过直接 prepare 任意候选绕过顺序；历史查看与自动选材分离。
6. `core/weekly.py`：仅限制新题池为经验证、与当前训练来源一致的可用材料；不得改评分、强化状态转换及已生成题目。缺素材时明确返回空池原因，不回退隔离库。
7. 新增 `backend/tests/test_source_governance.py`；按需更新既有 material、material_search、p1_candidates、p1_critical_fixes、migrations、legacy_migration、weekly_loop、exceptions 测试；不得放宽无关断言。
8. 本交接文档更新实施证据；`docs/LLA_Source_Governance_Verification.md` 记录来源验证、交付 SHA、Review/Verify 与风险。

最小性七项检查：仅配置不能解决持久状态与旁路；已有候选/入库/质量/SQLite 足以复用；优先修改既有函数；下载使用标准库；SQLite 事务/唯一约束与原子文件替换提供原生保障；使用已有 ASR/ffmpeg 依赖；必要新增仅为治理字段、索引及针对性测试。无新增框架、工厂、通用服务层或依赖。

本阶段不新增平台适配器；来源未获准时返回 SOURCE_NOT_APPROVED，不能偷偷启用备用。离线小规模验证通过注入现有 provider 测试接口实现。取得来源授权后返回 Plan Gate 确定单一适配器及最多 3 条连续原课的联网验证范围，再逐一接入，不同时接入所有平台。

## 数据与强制规则

- 复用 provider→source_platform、provider_item_id→source_item_id、duration_seconds→audio_duration、speech_rate_wpm→speech_rate、audio_quality→audio_quality_status；通过来源关联提供统一索引，不重复存同义字段。
- 补齐 source_course、course_order、item_order、accent、speaker_id（未知为空）、cefr_level、transcript_verified、license_status、ingestion_status、source_priority、imported_at，并保存口音/文字/授权证据、审核人/时间、失败码、尝试次数。audio_path、transcript、source_url 复用原字段。
- 来源身份唯一约束以 platform+item_id 为基础，明确同一课不同音频版本的身份；不同 URL、批次、scope 不得重复生成正式素材。历史重复不删除，隔离并保留关联，不能因添加索引破坏迁移。
- 旧素材默认 UNVERIFIED/隔离，不把 NULL 改成 en-GB；历史音频、句子、尝试、进度和已生成周测保留。隔离只影响新推荐/新入库/新题池，不擅自转换历史训练状态。
- Active Source 初始为空。明确确认和授权证据齐备才能启用；每周期一个主源。并发激活/切换需事务校验当前周期版本。
- 顺序为 CEFR A1→A2→B1，同级按平台课程、课号；未标等级进入待审，不能按课号臆造 CEFR。无某级内容明确记为不存在，不伪造或跨站填充。发现等级不等于学习资格：学习难度仍由现有八周机制及用户确认决定。
- en-US 直接排除；口音未知/混合未审、授权未清、稿音不匹配或质量不合格不入正式库。机器对齐或高 ASR 相似度不能自动等同 transcript_verified；缺少充分证据留待人工核验。
- 保留整篇原音；三段使用原始音频时间边界，不强切独立两分钟文件。短课仍必须满足既有三段结构、句子/时间戳和质量要求；不能为数量拼课、补 TTS 或放宽音质标准。
- 新下载写临时文件，完整性与解码校验后原子发布；断点以持久清单恢复。若服务器不支持 Range，重试单条下载即可，不强求字节级续传；403/429/登录限制不绕过。
- 状态至少区分待处理、待人工确认、不合格、可重试失败、已确认无法获取、已入库。临时故障不能当永久无法获取；每条保留原因和证据。
- 平台完成只针对明确记录的目录快照与范围；分页/目录发现必须完整，有游标不能报完成，未知总量不报 100%。分别报告发现覆盖、已分类比例、合法获取比例、入库比例和剩余条目。
- 切换前要求完整目录证据、全部条目有处置状态、无可处理合格项或临时失败、报告已生成；待人工条目必须显式列入报告和用户决策，不自动视为合格或已获取。用户针对当前报告版本明确确认下一平台后才能切换；新发现候选或报告变化使旧确认失效。

## 验收矩阵

1. 主源有未处理合格项：切换被拒；失败也不自动回退。
2. 混合输入 A1/A2/B1、课程/课号：严格平台原顺序；直接选择后续项不能绕过。
3. en-US：search、prepare、手工导入和旧 next 均不能进入正式训练库。
4. 未知口音、稿音不符、无授权及音乐/噪声待审：隔离，有原因；盲听接口不泄露全文。
5. 重复 URL/别名 URL、批次、scope、并发 prepare 和幂等重放：同一来源只有一个正式素材。
6. 下载中断/转码失败/准备提交前崩溃：重启后恢复；半文件不能当成功，不留半成品正式素材。
7. 目录未完成、仅部分发现、临时失败、授权缺失、零结果、全部终态：完成报告和分母正确；发现全部不等于合法获取全部。
8. 无确认、过期报告确认、并发切换、未授权新平台：拒绝；明确有效确认才切换。
9. 听写 exact、operation_id、Part、朗读、周测、八周升级、跨天恢复及累计时间：现有回归通过；只过滤新题池。
10. 在主库副本重复迁移并比较历史关键表内容、关联、文件哈希：无删除/覆写，无默认英音回填。

Target Tests：新增治理测试及上述现有素材/迁移/周测测试。
Regression：`.venv/Scripts/python.exe -m pytest`（使用临时目录/数据库，禁止测试修改主库）。
Static：沿用现有 compile、ruff（若已安装）及 `frontend` 中 `npm run build`；无现有独立 type 工具则 N/A，不为流程加依赖。
Runtime：临时 SQLite+本地文件+现有测试 API，模拟重启、事务中断、失败恢复和并发；用本地 fixture 验证机制，不将 fixture 测试冒充真实平台音质/授权验证。
Review：正确性、最小性、入口旁路、事务原子性、历史保护及最终 diff。

DSH 交付完整 SHA、变更清单、逐条测试结果、运行证据与剩余风险。Codex 仅在同 SHA 主仓 clean 状态正式验收；未通过不得 Close。真实来源接入与批量采集仍为未完成事项，不得用治理离线测试通过宣称整个素材来源任务 Closed。
