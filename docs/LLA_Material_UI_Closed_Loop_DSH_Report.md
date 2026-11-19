# 素材页闭环：DSH 交付报告

- 日期：2026-11-19
- 仓库根：`D:\CODEX\LLA`，分支 `master`
- 计划基线：`2cf9cc406314a112179c564a9f10c64e42af2db6`（见 `docs/LLA_Material_UI_Closed_Loop_DSH_Plan.md`）
- 本报告对应最终 commit 见文末「版本」
- 独立验收由 Codex 执行；本报告不宣称 Close

## 1. 做了什么（只新增闭环必需的部分）

| 能力 | 实现 | 复用 |
|---|---|---|
| 受控上传 | `POST /api/materials/intake/upload`（raw 二进制，不是 multipart：环境无 `python-multipart`，按计划避免新依赖） | 后缀白名单沿用 `AUDIO_SUFFIXES`，时长探测沿用 `probe_duration` |
| 暂存枚举与状态 | `GET /api/materials/intake/items` | 状态由「磁盘文件 + 既有 DB 列」推导，无平行状态机 |
| 文字稿保存 | `PUT .../items/{item_id}/transcript` | 保存即删除旧对齐，旧证据自动失效 |
| 对齐 | `POST .../items/{item_id}/align` | 复用 `asr_draft.transcribe_words` + `forced_alignment.align_lines`；模型未缓存时 409，不静默下载 |
| 审核 | `POST .../items/{item_id}/approve` | 复用 `LocalImportService.import_one` + 难度分；**服务端重新校验口音/许可/稿件/对齐归属** |
| 官方音频 | `POST /api/materials/intake/fetch-official` | 复用 `acquisition.fetch_official_pack`（缓存复用、CRC、主源门） |
| 主源激活 | `POST /api/materials/intake/activate-source` | 复用 `MaterialCandidateService.activate_source`，需显式 confirm + 许可与目录证据 |
| 页面 | `frontend/src/MaterialImportPanel.jsx` + 接入 `App.jsx` | 删除旧「手填服务器路径 + 每句固定 8 秒」导入入口 |

### 1.1 修改文件

新增：`backend/app/core/material_intake.py`、`backend/tests/test_material_intake_api.py`、`frontend/src/MaterialImportPanel.jsx`、本报告。
修改：`backend/app/api/routes.py`、`backend/app/api/schemas.py`、`backend/app/core/local_import.py`（抽出 `import_one` / `material_id_for`，行为不变）、`backend/app/main.py`（装配）、`frontend/src/App.jsx`、`frontend/src/styles.css`。
未改动：训练状态机、周测、Part 完成、operation_id、难度标定、数据库 schema（无迁移）。

### 1.2 API 契约（新增）

| 方法 | 路径 | 请求 | 响应要点 | 错误码 |
|---|---|---|---|---|
| GET | `/api/materials/intake/items` | — | `active_source/band/counts/items[]`（含 `status/status_label/blockers/alignment/transcript_text/can_approve/can_train`） | — |
| POST | `/api/materials/intake/upload?filename&title&platform&course&item_order` | body=音频字节 | 单个 item（含 `created`） | 400 `UNSUPPORTED_AUDIO`/`EMPTY_UPLOAD`/`FILE_TOO_LARGE`、409 `UNDECODABLE_AUDIO` |
| PUT | `.../items/{item_id}/transcript` | `{"text":"每行一句"}` | item（旧对齐已失效） | 404 `ITEM_NOT_FOUND`、409 `TRANSCRIPT_TOO_SHORT` |
| POST | `.../items/{item_id}/align` | `{"model":"small"}` | item + `alignment.lines_preview` | 409 `TRANSCRIPT_MISSING`/`MODEL_NOT_AVAILABLE`/`ASR_UNAVAILABLE` |
| POST | `.../items/{item_id}/approve` | `{accent,license_status,transcript_verified,quality_ok,confirm}` | item（`ingested=true`；重复调用 `already_approved=true`） | 400 `CONFIRMATION_REQUIRED`；409 `ACCENT_NOT_ELIGIBLE`/`LICENSE_UNVERIFIED`/`TRANSCRIPT_UNVERIFIED`/`QUALITY_REJECTED`/`ALIGNMENT_MISSING`/`ALIGNMENT_STALE`/`ALIGNMENT_INCOMPLETE`/`ALIGNMENT_LOW_COVERAGE` |
| POST | `/api/materials/intake/fetch-official` | — | 下载/解包摘要 | 409 `SOURCE_NOT_APPROVED`/`SOURCE_MISMATCH`/`DATABASE_MISSING` |
| POST | `/api/materials/intake/activate-source` | `{platform,license_reference,catalog_scope,confirm}` | 激活结果 | 400 `CONFIRMATION_REQUIRED`/`EVIDENCE_REQUIRED`、409 `SOURCE_NOT_ACTIVATABLE` |

## 2. 验证结果（本次实际数量）

| 命令 | 结果 |
|---|---|
| `pytest -q -p no:cacheprovider` | **223 passed / 0 failed**（新增 `test_material_intake_api.py` 11 条） |
| `pytest backend/tests/test_material_intake_api.py` | 11 passed |
| `compileall -q backend/app backend/tests scripts` | 退出码 0 |
| `npm --prefix frontend run build` | 退出码 0，`dist/index.html 0.41 kB`、`index-*.css 22.02 kB`、`index-*.js 269.62 kB` |
| `git diff --check` | 退出码 0 |

真实批次只读核对（数据库隔离，`LTA_PROJECT_ROOT`=仓库、`LTA_DATABASE_PATH`=临时）：
`counts = {audio_files:100, transcripts:5, aligned:5, pending_review:5, ingested:0, trainable:0}`，状态分布 `ALIGNED 5 / AUDIO_ONLY 95` —— 列表来自既有暂存文件，未触发下载。

## 3. 验收矩阵逐项结论

| 编号 | 结论 | 依据 / 说明 |
|---|---|---|
| A1 | **PASS（API）/ BLOCKED（浏览器点击）** | 上传接口测试通过（受控目录、初始不可训练）；页面已实现并构建成功，但**没有真实浏览器点击证据** |
| A2 | **部分 PASS** | 无稿→`AUDIO_ONLY`；不支持后缀/空文件→400；损坏音频→`UNDECODABLE_AUDIO`（代码分支，未造样本）；超限→`FILE_TOO_LARGE`（代码分支，未造 160MB 样本）；文件名只取 basename，落盘名由内容哈希生成 |
| A3 | **PASS** | 同字节重复上传返回同一 `item_id`、`created=false`、磁盘只有一份；审批重复调用返回 `already_approved` |
| A4 | **PASS** | 上述 100 文件真实批次只读展示正确，未下载、未标为已校对 |
| B1 | **PASS** | 无主源时 `fetch-official`→409 `SOURCE_NOT_APPROVED`；激活需 `confirm`+许可+目录证据；不存在隐式切换 |
| B2 | **BLOCKED（本 SHA）** | 上轮真实下载证据存在（`ENEN.ZIP` 59,306,568 B，sha256 `d185dff0…`，解包 100 个 mp3），但**未在本 SHA 通过 API 重跑**；按计划缓存命中不能替代首次下载证据 |
| B3 | **部分 PASS** | 缓存复用与 CRC/断点/重试在服务层测试覆盖（前轮）；**并发点击未验证** |
| C1 | **部分 PASS** | 对齐接口可用且拒绝缺稿/缺模型；真实音频对齐证据来自本会话 CLI（5 课 coverage 98.7–100%、0 未匹配），**未通过 API 在真实音频上跑过** |
| C2 | **PASS** | 改稿删除旧对齐；陈旧对齐（音频/稿件哈希不符）拒绝；未匹配行拒绝；缺模型 409 且明确"不静默下载" |
| C3 | **PASS** | 直接调用审批 API：缺 confirm/非 en-GB（含 en-US、en-AU）/许可未清/稿件未确认/音质未接受 全部拒绝，且不产生正式素材 |
| C4 | **PASS** | 完整证据+确认→入库且幂等；**审核通过不等于可选**：A2 素材在 A1 档位不可选，切到 STAGE_2 后才 `selectable_in_band=1` |
| D1 | **BLOCKED** | 真实素材尚未审核入库，无合格素材可开始训练；`can_train` 门控与训练入口回调已实现并测试 |
| D2 | **BLOCKED（浏览器）/ 设计满足** | 状态全部来自文件+DB，无客户端缓存；刷新/断网未在浏览器验证 |
| D3 | **PASS** | 223 passed；编译 0；构建 0；diff --check 0；生产指纹未变（见 §4） |

## 4. 隔离与生产边界

- 所有自动化验证使用 `LTA_DATABASE_PATH` 指向临时目录；`conftest.py` 数据安全护栏未绕过。
- 生产库 `data/language_training.sqlite3`：323,584 B，mtime `2026-11-19 14:47:05`，sha256 `2B85E08EC2E3B7BDED2CA16D1DF7281CEE9123F59B13B368F8A237863C25CF4F`（本轮工作期间未变化）。
- 缓存 `data/import/cache/ENEN.ZIP`：59,306,568 B，mtime `2026-11-18 17:53:12`（未改动）。
- 暂存文件仅被**读取**；`/align`、`/upload` 只有在用户操作时才写入。

## 5. 三层状态必须分开说

1. **音频可自动获取**：是（官方整包，单文件、CRC 校验、缓存复用），但本 SHA 未重跑首次下载。
2. **文字稿仍需准备与校对**：是。官方文字无授权、页面不可自动获取；ASR 只是草稿，不能替代逐字校对。
3. **素材已审核可训练**：否。当前库内 0 条入库素材；5 课仅有对齐结果、尚未人工校对与审批。

## 6. 未解决项

- 无真实浏览器点击证据（上传/对齐/审批/训练四条链路只有 API 级验证）。
- 未在本 SHA 重跑真实网络下载；并发点击未验证。
- 对齐接口未在真实音频上跑过（CLI 已跑通，接口路径共用同一函数）。
- 校对仍待用户完成；5 个 TXT 不得自动视为已校对。
