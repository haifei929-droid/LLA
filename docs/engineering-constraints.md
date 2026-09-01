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
