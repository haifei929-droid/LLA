# LLA 素材来源治理：交付与验证记录

日期：2026-11-17。工作目录 `D:\codex\LLA`。基线 `b8d6bca9b4dd93d33fc068943bbb6123641672bf`（clean）。

## 1. 交付身份

- 实现提交：本次交付的 git commit（Codex 用 `git rev-parse HEAD` 绑定；本文件与实现同一提交）。
- 父提交：`b8d6bca docs: approve source governance plan for DSH handoff`。
- 交付范围：`docs/LLA_Source_Governance_DSH_Plan.md` 列明的文件 + 该计划明确要求新增的两个文件（`backend/tests/test_source_governance.py`、本文档）。无其它新增文件、无新增依赖、无新增框架/服务层。

## 2. 变更清单

| 文件 | 变更 |
|---|---|
| `backend/app/db/schema.sql` | materials/候选表治理字段；候选表成为持久发现清单 |
| `backend/app/db/connection.py` | 增量迁移（ALTER + IF NOT EXISTS）；来源身份唯一索引（历史重复时降级为普通索引并记录状态）；治理索引在列迁移之后创建，避免旧库迁移失败 |
| `backend/app/core/material_candidates.py` | 主源周期、目录/游标、完成报告、确认切换、审核、课程顺序、持久清单、staging 原子下载、审计 |
| `backend/app/core/materials.py` | 事务内插入（素材+句子+初始进度+来源关联+入库状态）；默认 `QUARANTINED` |
| `backend/app/core/material_preparation.py` | 治理入口校验、课程顺序、幂等重放优先、单事务原子入库、失败回滚并保留可重试状态 |
| `backend/app/core/material_search.py` | 旧 next 入口改为治理流程；移除并行来源选择与两周升级旁路 |
| `backend/app/core/transcript_validator.py` | `transcript_verification`：机器对齐/ASR 相似度不等于已核验 |
| `backend/app/core/weekly.py` | 新题池仅取已入库且与当前主源一致的已验证材料；空池显式报错 |
| `backend/app/api/routes.py`、`api/schemas.py` | 主源状态/启用/报告/确认切换、候选清单（不含 transcript）、审核、prepare 治理错误码；手工导入 en-US 拒绝、其余隔离 |
| `backend/app/main.py` | 不再注册默认 VOA/BBC provider；治理服务装配 |
| `frontend/src/CandidatePanel.jsx`、`App.jsx` | 主源与报告、待审/隔离/失败原因与顺序、审核与确认操作、取消 15–20 默认区间、导入文案 |
| `backend/tests/test_source_governance.py`（新增） | 治理验收矩阵 1–8、10 与 API 面 |
| `backend/tests/test_p1_candidates.py`、`test_p1_critical_fixes.py`、`test_material_search.py`、`test_exceptions.py`、`test_weekly_loop.py`、`test_migrations.py` | 既有回归适配治理入口；断言不放宽 |
| `docs/LLA_Source_Governance_DSH_Plan.md` | 追加实施证据 |
| `docs/LLA_Source_Governance_Verification.md`（新增） | 本文件 |

## 3. 与验收矩阵的对应

1. 主源仍有未处理合格项/可重试失败 → `confirm_switch` 返回 `SOURCE_ITEMS_PENDING`；provider 失败不切换来源（`test_activation_requires_evidence_and_blocks_implicit_sources`、`test_switch_is_rejected_while_retryable_failures_remain`）。
2. 混合 A1/A2/B1 与课程/课号 → 严格平台原顺序，直接 prepare 后续项得 `COURSE_ORDER_VIOLATION`（`test_course_order_cannot_be_bypassed`）。
3. en-US → 发现阶段直接排除（不下载）、手工导入 400、prepare 拒绝（`test_en_us_is_excluded_from_discovery_import_and_ingestion`）。
4. 未知/混合口音、授权未清、CEFR 未标、稿音未验 → `PENDING_REVIEW` 并留原因；候选清单接口不返回 transcript 全文（`test_quarantine_reasons_and_blind_listing`）。
5. 重复 URL/别名 URL/批次/scope/并发 prepare/幂等重放 → 同一来源只有一个正式素材（`test_duplicate_identity_and_idempotent_replay`）。
6. 下载中断/解码失败/提交前崩溃 → 单事务回滚，无半成品素材，候选保留可重试（`test_failed_ingestion_leaves_no_half_material`、`test_download_failure_is_retryable_not_terminal`）。
7. 目录未完成/有游标/未知总量/零结果 → 报告分母与完成状态正确，发现≠合法获取≠入库（`test_completion_report_denominators`）。
8. 无确认/过期确认/并发切换/未授权新平台 → 拒绝；有效确认才切换（`test_switch_requires_current_report_and_explicit_confirmation`、`test_concurrent_activation_keeps_one_cycle`）。
9. 听写 exact、operation_id、Part、朗读、周测、八周升级、跨天恢复 → 未改状态机；仅新题池过滤（`test_weekly_loop.py`、`test_p1_critical_fixes.py` 等既有回归）。
10. 重复迁移 → 历史素材/句子/进度保留，accent 不回填，隔离但保留（`test_migrations.py`、`test_quarantined_material_is_isolated_from_the_new_weekly_pool`）。

## 4. 本轮已做的本地检查（DSH）

- `python -m compileall backend/app backend/tests`：通过。
- `python -c "import app.core.material_candidates, ... app.main"`：导入通过。
- 纯函数抽查（`assess_disposition`、`normalize_accent`、`transcript_verification`、`_build_report`、`_course_key`）：结果符合规则。
- 内存库 schema/写入一致性抽查（`:memory:` + `schema.sql` + 迁移索引，不触碰仓库文件与主库）：materials/candidate/quality-report 插入列与参数一致，`uq_material_candidates_source_identity` 生效（不同 scope/别名 URL 的同一来源身份被拒），`source_priority` 计算正确。
- JSX 语法检查：`frontend/node_modules/@esbuild/win32-x64/esbuild.exe` 直接转译 `CandidatePanel.jsx`、`App.jsx` 成功（exit 0）。
- `npm run build`：在 DSH 沙箱内被拒绝（esbuild 子进程 `spawn EPERM`，属沙箱管道限制，非代码问题）→ 交由 Codex 在标准环境执行。
- `ruff`：未安装 → N/A（不为流程加依赖）。
- 本轮未执行 pytest，未执行任何运行态/联网验证，未对主库写入。

## 5. 待 Codex 独立执行（本轮未运行）

```bash
.venv/Scripts/python.exe -m pytest backend/tests/test_source_governance.py -q
.venv/Scripts/python.exe -m pytest backend/tests/test_p1_candidates.py backend/tests/test_p1_critical_fixes.py -q
.venv/Scripts/python.exe -m pytest backend/tests/test_material_search.py backend/tests/test_exceptions.py -q
.venv/Scripts/python.exe -m pytest backend/tests/test_weekly_loop.py backend/tests/test_migrations.py -q
.venv/Scripts/python.exe -m pytest          # full regression
cd frontend && npm run build
```

运行态验证（主库副本上的重复迁移与历史表比对、并发/回滚故障注入）同样由 Codex 在临时目录执行；本轮未对主库执行任何写操作。

## 6. 剩余风险

- `app_metadata` 内的发现清单按批次整体落盘；进程在批次中途崩溃时，重跑会依据候选表与目录重新推导各条目处置（不会产生终态误判，但崩溃批次的临时条目计数需要重跑补齐）。
- 真实平台目录快照、逐课 CEFR/口音/稿音证据仍未核验；本阶段只有 fixture 级离线验证，不能替代真实来源授权与实测。
- 主源切换后，旧主源已入库材料不再进入新题池（符合"与当前训练来源一致"），但历史训练数据与已生成题目保持原样。
- 手工导入一律隔离：产品层若需"本地上传即训练"，需后续 Plan Gate 明确其治理证据来源。
- 未接入新平台适配器；真实接入需回到 Plan Gate 确定单一适配器与 ≤3 条联网验证范围。
