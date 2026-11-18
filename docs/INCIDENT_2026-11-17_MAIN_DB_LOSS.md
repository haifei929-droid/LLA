# 事故记录：主数据库误删（2026-11-17）

> 状态：已确认、已止损、数据未恢复（用户选择方案 A：接受损失、按 schema 重建）。本文档是这次事故的事实记录与防护依据。

## 1. 一句话结论

2026-11-17 20:37:34，一次临时核验脚本在**生产数据根目录** `D:\CODEX\LLA\data` 执行了**非递归**的 `glob("*.sqlite3")` + `unlink`，删除了主数据库 `data/language_training.sqlite3`（708,608 字节）与 `data/p2_fixtures.sqlite3`（299,008 字节）。两个文件都被 `.gitignore` 中的 `*.sqlite3` 排除，远端仓库与 Windows 备份/卷影副本中均无副本；TestDisk 提取的文件已全零，PhotoRec 未找到可用副本。**学习历史不可恢复**，音频素材与代码未受影响。

## 2. 时间线（+08:00）

| 时间 | 事件 | 证据 |
|---|---|---|
| 11-17 19:52 | `data` 目录清单同时存在主库 708,608 B 与 fixture 库 299,008 B | Codex 会话只读扫描输出 |
| 11-17 20:37:32 | 核验脚本被追加清理逻辑：`for stale in Path(r"D:\codex\LLA\data").glob("*.sqlite3"): stale.unlink()` | DSH 会话 `tool/call`（edit） |
| 11-17 20:37:34 | 脚本执行，两个数据库被删除；脚本自身的核验输出继续正常打印 | DSH 会话 `tool/result` |
| 11-17 20:39:46 | Codex 巡检发现 `data` 下只剩 8 个临时核验库 | 会话 tool 结果 |
| 11-18 10:35 | 形成恢复证据清单：git 615 个对象 0 个 SQLite、远端无 DB 提交、Windows 备份 none | `LLA-recovery-evidence.json` |
| 11-18 10:39 | 形成删除时间线（回溯 DSH 会话日志） | `LLA-deletion-timeline.json` |
| 11-18 10:46 | TestDisk 在 NTFS「Deleted files」中找到两个文件并复制到 C 盘 | `testdisk.log` |
| 11-18 10:49 | DSH 隔离确认书落地（`DSH_ISOLATION_ACK.md`） | `dsh-isolated/` |
| 11-18 10:51–10:56 | PhotoRec 两次扫描：空闲区 0 命中；全空间 120 个 sqlite 命中，均非主库 | `photorec.log` |
| 11-18 从 10:02 起 | 用 procdump 抓取后端进程内存（1.99GB）用于取证 | `lla-server-9420.dmp` |
| 11-18 16:0x | 代码锁存（push + bundle）并按方案 A 重建空库 | 见 §6 |

## 3. 根因（并列事实，不互为前提）

1. **生产数据与测试执行没有物理隔离**：清理动作的作用点是 `data/`（生产数据根），而不是任何隔离副本。
2. **通配删除且没有显式清单**：`glob("*.sqlite3")` 的匹配范围在真实数据目录下等于不可预知的批量删除；删除前未枚举、未留存清单、未二次确认。
3. **数据没有任何仓库外副本**：`*.sqlite3` 被 gitignore，远端无、备份无、卷影无。一旦文件消失就没有回退点。
4. **工作副本边界不清**：在“只允许修改某范围”的许可之外执行破坏性操作，使一次误操作直接落在不可重建的资产上。

## 4. 恢复尝试与结果（已穷尽）

| 途径 | 结果 |
|---|---|
| Git 历史 / 615 个对象 | 0 个 SQLite 对象；`*.sqlite3` 从未入库 |
| GitHub 远端（master / releases） | 无数据库提交、无 Release 备份 |
| 回收站（C:/D:）/ Windows 备份 / 卷影副本 | 无 |
| TestDisk 7.2 NTFS Deleted files | 找到两条记录（日期、大小与删除前一致），但复制出的主库 708,608 B 中**文件头全 0、非零字节仅 8,706**，只有第 136/145/158 页非零（其中一页为 `RIFF/WAVE` 头，属他人数据混入）；fixture 库 **全 0**。`PRAGMA integrity_check` 无法执行（`file is not a database`） |
| PhotoRec 空闲区扫描 | 0 文件 |
| PhotoRec 全空间扫描 | 120 个 sqlite 命中（最大 12.6 MB），均为其他库（多为 311,296 B 的 pytest 临时库），无 708,608 B 主库 |
| 后端进程内存转储（1.99 GB） | 命中 2 处完整 page-1：page size 4096、`db_size_pages = 173`（= 708,608 B，与主库一致），含完整 schema 与大量行级字符串（`web-6me-170` 1,522 次、`preset-002` 698 次等）；但 page-1 之后的 4 KB 块不连续，**只能做碎片级抢救，不能还原成完整库** |

**结论：磁盘层面不可恢复；内存转储只在需要对个别记录做取证式抢救时才有价值。**

## 5. 损失与保留

损失（学习历史）：

| 表 | 事故后 manifest 记录行数 |
|---|---|
| materials | 7 |
| sentences | 759 |
| training_progress | 7 |
| dictation_attempts | 138 |
| dictation_operations | 15 |
| weekly_assessments / weekly_gate_records | 8 / 8 |
| reading_attempts | 8 |
| listening_memory / memory_recognition_episodes | 22 / 10 |
| difficulty_events | 11 |
| comprehension_checks | 4 |
| training_difficulty_profiles | 1 |

> 数据来源为 `LLA-recovery-evidence.json` 中 `baseline` 与 `check` 两份**完全相同**的快照，只能当作“事故后整理的行数记录”，不是删除瞬间的精确值。

保留（未受影响）：代码与文档（git）、`data/materials` 373 个音频、`data/processed/web` 10 个源文件、`data/processed/p1-candidates` 46 个 wav、`data/recordings` 24 个 wav。

## 6. 处置决定与执行

- **方案 A（用户确认）**：接受学习历史损失，按 `backend/app/db/schema.sql` 重建空库。
- 重建结果：`data/language_training.sqlite3`，323,584 B，26 张表、47 个索引，`PRAGMA integrity_check = ok`，二次 `Database.initialize()` 幂等；`app.main` 可导入。
- **代码锁存**：`origin/master` 已更新到 `37231ee`；另有全 ref bundle：
  - `D:\CODEX\LLA-backups\LLA-allrefs-37231ee-20261118-160234.bundle`（899,319 B）
  - 同 hash 副本 `C:\Users\Administrator\LLA-backups\LLA-allrefs-37231ee-20261118-160234.bundle`
  - SHA256 `98FE2E7907338206F9CCD734743C58D676F5CF4D5B7FB4D6A4A2F766379BBBBA`
  - 恢复方式：`git clone <bundle> LLA-restored` 或 `git fetch <bundle> 'refs/*:refs/restored/*'`。

## 7. 本次新增的防护（同一个提交内）

| 防护 | 文件 | 作用 |
|---|---|---|
| 测试运行时强制隔离 | `backend/tests/conftest.py` | 全会话把 `LTA_DATABASE_PATH` 等指向仓库外临时目录；若继承值指向 `data/` 直接中止；会话前后对生产库做 sha256 指纹比对，任何变化即失败 |
| 通配/递归删除静态拦截 | `backend/tests/test_data_safety.py` | 扫描 `backend/app`、`backend/scripts`、`scripts`：禁止 `rmtree`、`Remove-Item -Recurse`、`del /s`，禁止 glob/rglob/Get-ChildItem 馈入删除调用 |
| 备份工具 | `scripts/backup_database.py` | SQLite 在线备份 + `integrity_check` + sha256 sidecar + `backup-index.jsonl`；备份目录必须在仓库外；保留份数仅按显式前缀清单轮转 |
| 备份调度 | `scripts/register_backup_task.ps1` | 注册每日计划任务（默认 12:00）调用上述脚本，可 `-RunNow` / `-Unregister` |
| 工程约束 | `docs/engineering-constraints.md` §6–§8 | 把数据目录纪律、备份要求、本事故教训写成强制约束 |

## 8. 尚未解决的风险

1. 备份任务默认以当前用户身份运行，用户未登录时不执行；需要覆盖关机/未登录场景得改用 SYSTEM 账户或 Task Scheduler GUI 配置。
2. 备份只落到同一台机器的另一块盘（`D:\CODEX\LLA-backups`），不防整机/双盘损毁；如需抗灾需外接盘或远端对象存储。
3. 重建后的库是空的：`preset-001/002` 音频虽在磁盘上，但对应 DB 行已丢失；按现行素材治理规则重新导入会被判为 `QUARANTINED`（口音/授权未验证），需按来源治理流程重新取证。
4. 内存转储（1.99 GB）保留在 `C:\Users\Administrator\.codex\visualizations\2026\11\17\01a1e958-3c65-7450-a5d1-f92d02da9f45\lla-server-9420.dmp`，属于取证材料，未纳入备份，可按需清理。