# LLA 工程约束（Engineering Constraints）

> 固定工程约束。所有 LLA 开发、测试、验收、脚本执行必须遵守。本文档是唯一权威，任何偏离需先更新本文档。

## 1. 标准仓库根目录

LLA 标准仓库根目录固定为：

```
D:\CODEX\LLA
```

Windows 文件系统大小写不敏感，`D:\codex\LLA` 与之等价。

## 2. 每次开发/验收前必做检查

进入任何开发或验收前，必须先执行并确认以下三项，三项全部通过才能继续：

```bash
git rev-parse --show-toplevel
git rev-parse HEAD
git status
```

要求：

- `git rev-parse --show-toplevel` 必须返回标准仓库根目录（`D:/codex/LLA`，即 `D:\CODEX\LLA`）。
- 记录当前 `HEAD` SHA，验收必须明确 target commit SHA。
- `git status` 必须 clean（无未提交修改）。
- 如果当前 HEAD 与指定 commit 不一致，停止开发/验收，不得直接给出 PASS/FAIL。

## 3. 交付与验收绑定 SHA

- DSH 每次开发交付必须提供 commit SHA。
- Codex 正式验收必须绑定该 commit SHA 作为验收目标。
- 如果验收环境 HEAD 与目标 SHA 不一致，必须停止验收，不得输出 PASS/FAIL。

## 4. 禁止使用旧 worktree / stale commit 作为验收基线

- 不允许使用旧 detached worktree、stale commit 作为正式验收基线。
- 如确需额外 worktree，必须明确用途和目标 commit，不得默认作为正式验收目录。
- 正式验收必须在标准仓库根目录 `D:\CODEX\LLA` 的指定 SHA 上执行。

## 5. 历史教训

2026-08-28 前后，验收环境曾误用 `C:\Users\Administrator\.codex\worktrees\9626\LLA`（detached HEAD `a74c2dd`）作为验收基线，而开发侧实际在 `D:\CODEX\LLA`（HEAD `e743a5b`）。二者 commit 错位，导致「已实现 / 未实现」的重大不一致。此后固定本约束，杜绝同类问题。

## 6. 数据目录纪律（2026-11-17 主库误删事故后新增）

- 生产数据目录 `data/` 只允许由应用本体、`scripts/backup_database.py` 与人工确认后的迁移脚本写入。
- 测试、探针、临时核验脚本**必须**使用隔离运行时：通过 `LTA_DATABASE_PATH` / `LTA_MATERIALS_DIR` / `LTA_RECORDINGS_DIR` / `LTA_PROCESSED_DIR` 指向仓库外的独立目录。`backend/tests/conftest.py` 已强制执行该隔离：继承环境变量若指向 `data/` 会直接中止测试。
- 禁止以通配符或递归方式删除任何文件：不得出现 `glob(...)`/`rglob(...)`/`Get-ChildItem` 馈入删除、`shutil.rmtree`、`Remove-Item -Recurse`、`del /s` 等写法。`backend/tests/test_data_safety.py` 会静态拦截。
- 任何删除动作必须来自**显式清单**：先把「将被删除的确切绝对路径」写下来并核对，再逐个删除；禁止先跑通再补清单。
- 不得为了跑通测试而关闭、绕过或删除上述守卫；需要放宽必须先改本文件并说明理由。

## 7. 备份与代码锁存

- 每日备份：由 `scripts/register_backup_task.ps1` 注册计划任务，调用 `scripts/backup_database.py`（SQLite 在线备份 + `PRAGMA integrity_check` + sha256 索引）。
- 备份目录固定在仓库外（默认 `D:\CODEX\LLA-backups\db`，可用 `LLA_BACKUP_DIR` 覆盖）；备份文件不得提交进 git。
- 代码必须同时存在于远端与本地 bundle：除 `git push` 外，定期 `git bundle create <path> --all` 落到另一块盘。
- 数据库文件永久不入 git（`*.sqlite3`），因此 **git 不是数据备份**，任何“代码有备份所以数据安全”的推断都视为无效。

## 8. 历史教训（数据）

2026-11-17 20:37，一次临时核验脚本在 `D:\CODEX\LLA\data` 执行非递归 `glob("*.sqlite3")` + `unlink`，删除生产库 `data/language_training.sqlite3`（708,608 字节）与 `data/p2_fixtures.sqlite3`。git、GitHub、回收站、卷影副本均无副本；TestDisk 提取文件已全零；PhotoRec 未找到。详见 `docs/INCIDENT_2026-11-17_MAIN_DB_LOSS.md`。

结论三条：**数据与测试执行必须物理隔离；删除必须来自显式清单；备份必须存在于仓库之外。**