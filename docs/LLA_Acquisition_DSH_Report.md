# LLA 自动采集：DSH 开发交付报告

- 日期：2026-11-18
- 仓库根：`D:\CODEX\LLA`
- 开发执行：DSH；独立验收：Codex

## A. 开发结果

### A.1 采用来源与选择依据

按任务要求的顺序评估三家，结论如下（依据与原文引用见
`docs/LLA_Primary_Source_Authorization_Assessment.md`）：

| 顺序 | 平台 | 音频 | 文字稿 | 结论 |
|---|---|---|---|---|
| 1 | 50languages / book2 (English UK) | 官方整包 `ENEN.ZIP`，音频 CC BY-NC-ND 3.0 US，可非商业分享与本地保存 | **无许可**（All rights reserved），且课程页面对自动化请求间歇性 403 / ToS §6 禁止批量自动获取 | **选为唯一 Active Source，但只自动获取音频整包** |
| 2 | British Council LearnEnglish | ToS 禁止"以任何方式修改"与复制进产品；边缘对自动请求一律 403 | 同左 | 排除（记录阻塞原因） |
| 3 | Breaking News English | 版权页点名禁止 app/LMS/CMS，仅允许纸质打印 | 同左 | 排除（记录阻塞原因） |

因此"音频与文字稿都全自动获取"在当前三家上**不成立**：
文字稿的自动获取要么违约（50languages §6）、要么被拒（BC 403）、要么被明文禁止（BNE）。
本实现遵从上位规则"不得绕过访问限制"：**音频自动获取已真实运行，文字稿仍是人工/ASR 步骤。**

### A.2 实现功能

| 能力 | 实现 |
|---|---|
| 单一主源锁定 | `acquisition.SOURCES` 只在策略层列出候选；`first_automation_allowed()` 只返回第一个允许自动获取的平台；`assert_can_automate()` 对被禁平台抛 `SOURCE_BLOCKED_BY_POLICY` |
| 英音筛选 | 采集产物以 `accent_claim=en-GB` 记录，但**不自动写入 accent 列**；进池仍由 `material_pool` 的 `accent='en-GB'` 硬门槛把关 |
| 音频自动获取 | `fetch_official_pack()`：单文件下载 + sha256 + zip CRC 校验 + 解包 100 个 mp3 到 `data/import/inbox/<batch>/` + 写 `batch.json` |
| 文字稿 | 不自动抓取；`scripts/fetch_materials.py --draft-asr` 可用本地 ASR 生成草稿（须人工校对），或用浏览器保存页面 + `scripts/extract_phrasebook_text.py` |
| 完整性/质量/去重 | zip CRC、导入时逐条校验；重复导入按 `source_platform+course+item` 幂等跳过；音质与稿音核验在审批阶段 |
| 失败与断点 | 下载失败抛 `AcquisitionError`；缓存命中则复用（`--force` 才重下）；导入失败逐条记录 `failed[]` 可重跑 |
| 储备补充 | `reserve_plan(ready, per_day, days)` 纯函数：`needed=ceil(per_day*days)`，`shortfall=needed-ready`；`--topup` 在缺料时才触发采集 |
| 训练流程兼容 | 未改动训练状态机、Part 完成、operation_id、周测、八周升级；采集只往暂存目录写文件，数据库写入仍走既有 `LocalImportService` |

### A.3 修改文件

新增：
- `backend/app/core/acquisition.py`（308 行）
- `backend/tests/test_acquisition.py`（75 行）
- `scripts/fetch_materials.py`（115 行）
- `data/import/.gitkeep`

修改：
- `.gitignore`：忽略 `data/import/*`（本次真实下载 118.6 MB 素材，绝不入库）

### A.4 本地自测结果（含真实网络验证）

| 项 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `pytest -q -p no:cacheprovider` | **192 passed / 0 failed** |
| 新增采集用例 | `pytest backend/tests/test_acquisition.py` | 5 passed（策略拒绝、首选源、储备算法、解包与元数据、探测结构） |
| **真实探测** | `fetch_materials.py --probe` | 音频包 `200 / 59,306,568 B / application/zip`；测试课页 `206 + served_challenge=false`（间歇可达）；BC `403 + challenge`；BNE `206`（但条款禁止） |
| **真实采集** | `fetch_materials.py --fetch` | 下载 **59,306,568 B**，sha256 `d185dff0380e42bbf11acd041de0fd602d6e5194ee0c9d7bca45b0ad505e3ec1`，zip CRC 通过，**解包 100 个 mp3**，写 `batch.json` |
| 储备补充计划 | `fetch_materials.py --topup --dry-run --days 7` | `needed=7, ready=0, shortfall=7, should_topup=true`（未执行下载） |

产物位置：`data\import\cache\ENEN.ZIP`、`data\import\inbox\book2-en-uk\ENEN001…100.mp3`（均被 git 忽略）。

## B. 版本信息

- 仓库根：`D:\CODEX\LLA`
- Commit SHA：见交付时的 `git rev-parse HEAD`
- 工作区：clean（采集产物被忽略）

## C. Codex 必须验证的要点

1. **单一来源锁定**：`assert_can_automate("BRITISH_COUNCIL")` / `("BREAKING_NEWS_ENGLISH")` 必须抛 `SOURCE_BLOCKED_BY_POLICY`；`first_automation_allowed()` 只能是 50LANGUAGES。
2. **英音筛选**：采集不写 `accent`；导入后 `accent` 为 NULL、`transcript_verified=0`；未审批素材不得出现在 `/api/p1/pool`。
3. **自动采集真实运行**：重跑 `--fetch`（或删缓存后）应得到 100 个 mp3 与同一 sha256；zip CRC 必须通过。
4. **入库与去重**：`import_local.py` 二次执行应全部 `DUPLICATE_IDENTITY`；`--approve` 后才 `selectable_in_band` 从 0 变 1。
5. **网络中断不影响学习**：断网时 `--fetch` 报错退出，不触碰数据库；训练选材走本地池。
6. **训练机制回归**：192 passed 基线不得退化；`test_source_governance` / `test_weekly_loop` / `test_p1_*` 全绿。

## D. 已知限制

1. **文字稿没有自动获取**：50languages 文字无授权且 §6 禁止批量自动获取；BC/BNE 被条款排除。文字稿只能人工（浏览器保存 + 提取器）或 ASR 草稿 + 人工校对。
2. **音频授权为 CC BY-NC-ND**：可非商业复制，但**不得分发修改版**；按句切分可能构成改编，仅限个人本地使用，不得对外分发。
3. **ASR 草稿未经真实音频验证**：`--draft-asr` 仅单测覆盖；首次运行需下载 whisper 模型（约 140 MB），本报告未在真实课程音频上跑通。
4. **口音仍非自动判断**：整课音频男女声混读，自动口音识别不可靠，仍以课程标注 + 人工确认为准。
5. **未做前端**：采集/池只有命令行与 API，无 UI。
6. **未宣称"全自动素材库"**：音频自动、文字人工，端到端仍需一次人工审批。

## E. 步骤 1 收尾：数据安全与缓存（2026-11-19）

针对 Codex 复验提出的收尾项，四项改动与实测：

| 问题 | 修复 | 实测 |
|---|---|---|
| 下载异常遗留 `.part` | 每次 fetch 前清理遗留 `.part`；下载仍写 `.part`，校验通过后原子 `replace()` 为缓存 | `test_stale_part_file_is_removed_before_fetching` 通过 |
| 损坏旧缓存被信任 | 缓存先做 zip CRC 校验；损坏则**改名隔离为 `<name>.corrupt`**（保留证据），再重新下载 | `test_corrupt_cache_is_quarantined_then_replaced` 通过，`quarantined_cache` 字段记录路径 |
| `--topup --dry-run` 写副作用 | 新增 `require_database()`：缺库 → `DATABASE_MISSING`（不创建文件）；缺治理表/缺池列 → `SCHEMA_OUTDATED`；schema 检查用**只读连接**（`mode=ro&immutable=1`），不做任何写入；迁移只能由 `--migrate` 显式触发 | 隔离库实测：旧库 `--topup --dry-run` → `SCHEMA_OUTDATED` 且文件哈希不变；`--migrate` 后才输出计划 |
| 有效缓存保留 | 校验通过的缓存直接复用（`cached: true`），不重下 | `test_valid_cache_is_reused_without_downloading` 通过 |

补充：`--fetch` 同样走 `require_database()`（不再 `initialize()`），因此它在缺库/旧库上只会报错，不会隐式建库或迁移。

**验证纪律**：以上所有验证均在 `LTA_*` 指向的临时数据库与临时收件箱上完成，未再触碰生产库。

**已知遗留（下一轮处理）**：生产库在 2026-11-18 17:53 因一次 `--topup --dry-run` 被迁移（新增 `difficulty_score`/`difficulty_evidence`，数据未变、`integrity ok`）；该缺陷本身已在本轮修复，历史副作用不可回滚，无需处理数据。
