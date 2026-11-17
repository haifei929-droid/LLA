# Codex Engineering Workflow V2

## 1. Purpose

本文件定义后续项目统一使用的工程开发与验收基线。

核心目标：

- 控制 Scope；
- 减少过度设计；
- 保证开发结果可验证；
- 保持 DSH 开发与 Codex 独立验收的职责分离；
- 避免已 Closed 问题被重复打开；
- 用运行证据而不是主观判断决定任务是否完成。

统一流程：

```text
Requirement
↓
PLAN GATE
↓
DSH IMPLEMENTATION
↓
REVIEW GATE
↓
VERIFY GATE
↓
CLOSE
```

---

# 2. Roles

## DSH

负责：

- 按 Approved Plan 实现代码；
- 执行最小必要修改；
- 修复 Review / Verify 中确认的阻断问题。

不得：

- 自行扩大 Scope；
- 顺手重构；
- 顺手修复无关问题；
- 自行新增 dependency / architecture；
- 修改已 Closed 功能；
- 降低 Acceptance Criteria。

---

## Codex

负责：

1. 开发前 Plan；
2. 开发后 Independent Review；
3. 最终 Verification；
4. 根据证据给出 PASS / FAIL / Close Recommendation。

Codex 不负责：

- 主动扩大需求；
- 因代码风格重新设计系统；
- 把邻近问题自动加入当前任务；
- 因发现 P2 优化项阻止正常 Close。

---

# 3. Gate 1 — PLAN

## 3.1 Problem Understanding

开发前必须先检查 repository 当前真实实现。

明确区分：

- Confirmed Issue
- Inferred Risk
- Out of Scope Issue

不得把推测视为已确认缺陷。

---

## 3.2 Minimal Solution Check

在新增以下内容之前：

- module
- class
- abstraction
- wrapper
- utility
- dependency
- configuration
- database table
- service layer

必须依次回答：

1. 能否不新增代码解决？
2. 当前 repository 是否已有能力可以复用？
3. 是否可以修改已有逻辑，而不是增加 abstraction？
4. 标准库是否已经能够解决？
5. Framework / Browser / OS / Platform 是否已有原生能力？
6. 当前已有 dependency 是否已经能够解决？
7. 如果仍必须新增，最小必要实现是什么？

原则：

> 无法证明现有能力不足时，不新增新的工程结构。

禁止：

- speculative abstraction；
- 为未来假设提前设计；
- 单一实现先造 factory；
- 无必要 wrapper；
- 简单需求新增 dependency；
- 重复实现已有 utility。

但不得为了减少代码牺牲：

- correctness
- validation
- error handling
- security
- data integrity
- 已明确要求的 edge cases
- 必要兼容性

---

# 4. Scope Definition

每个 Plan 必须明确：

## IN SCOPE

- 本次需要修改的行为；
- 预计修改文件；
- 必要调用链；
- Acceptance Criteria。

## OUT OF SCOPE

- 无关问题；
- Future Architecture；
- 不需要修改的模块；
- 已 Closed 功能；
- 本任务未批准的重构。

发现邻近问题时：

```text
Record
↓
Backlog
```

不得自动加入当前 Scope。

---

# 5. Implementation Plan

计划按照最小修改原则拆解：

```text
Step 1
- Modification
- Files
- Reason
- Existing capability reused: YES / NO

Step 2
...
```

如果需要新增：

- abstraction；
- dependency；
- data structure；
- service；
- configuration；

必须说明：

```text
Why existing capability is insufficient
```

---

# 6. Test / Acceptance Plan

至少定义：

- Target Tests
- Regression Tests
- Runtime Verification
- Architecture / State Boundary
- Negative Cases（如适用）

## BUG Fix

必须建立：

```text
Reproduction
↓
Before Fix = FAIL
↓
Implementation
↓
After Fix = PASS
```

不能只证明新代码能够运行。

---

# 7. Plan Gate Result

最终只能输出：

```text
PLAN STATUS:
READY FOR IMPLEMENTATION
```

或：

```text
PLAN STATUS:
NOT READY
```

如果 READY：

```text
MINIMALITY CHECK:
PASS
```

Minimality Check 未通过，不进入开发。

---

# 8. DSH Implementation Rules

DSH 必须遵守：

```text
Approved Plan
↓
Implementation
```

禁止：

- Scope expansion；
- unrelated refactor；
- speculative architecture；
- unauthorized dependency；
- 修改已 Closed 项；
- 为让测试通过改变原 Acceptance Criteria。

如果开发中发现必须改变原 Plan：

```text
PLAN DEVIATION
↓
Return to Plan Gate
```

Plan Deviation 包括但不限于：

- 新增未计划文件；
- 新 dependency；
- 新数据库结构；
- 新 service；
- 改变 architecture boundary；
- 明显扩大调用链。

---

# 9. Gate 2 — REVIEW

Review 分为：

```text
Correctness Review
+
Minimality Review
```

---

## 9.1 Correctness Review

重点检查：

- logic
- state transition
- persistence
- data consistency
- rollback
- exception handling
- API contract
- security boundary
- architecture boundary
- regression risk

Review 范围：

```text
Changed Files
+
Required Call Sites
+
Approved Acceptance Criteria
```

只有以下情况允许越出 Scope：

- 数据损坏；
- 安全风险；
- 明确业务回归；
- 已确定 architecture boundary 被破坏。

---

## 9.2 Minimality Review

检查当前 diff 是否存在明显：

- duplicated capability
- unnecessary abstraction
- unnecessary wrapper
- unnecessary dependency
- unnecessary configuration
- speculative future design
- excessive changed files
- duplicated utility
- 未复用已有实现

只报告高置信问题。

以下可以阻断：

- 无必要 dependency；
- 重复实现已有能力；
- 明显无必要 abstraction；
- 大量无关文件修改；
- 提前实现 Future Architecture；
- 新工程结构无法证明必要性。

以下通常不阻断：

- 少量 helper；
- 多写少量但清晰的 defensive code；
- 命名不是最简；
- 可以减少少量 LOC；
- 个人代码风格偏好。

---

# 10. Review Result

输出：

```text
CORRECTNESS:
PASS / FAIL

MINIMALITY:
PASS / FAIL

FINAL:
REVIEW PASS
```

或：

```text
FINAL:
REVIEW FAIL
```

P2 优化项默认不阻断 Close，除非原 Acceptance Criteria 明确要求。

---

# 11. Gate 3 — VERIFY

Verification 目标：

> 证明实际代码在真实执行中满足 Acceptance Criteria。

标准流程：

```text
Scope Check
↓
Build / Compile
↓
Lint / Type
↓
Target Tests
↓
Full Regression
↓
Runtime Verification
↓
Architecture Boundary
↓
Final Diff
```

---

## 11.1 Scope Check

确认：

- 最终 diff 与 Approved Plan 一致；
- 不存在 unexpected changes；
- 未修改 Out of Scope；
- 未重新打开 Closed 项。

---

## 11.2 Static Verification

按照当前项目已有工具执行：

- build
- compile
- lint
- type check

项目不存在的工具标记：

```text
N/A
```

不得为了满足本流程新增无必要工具。

---

## 11.3 Target Tests

运行本任务直接相关测试。

BUG 必须证明：

```text
Original Reproduction Case
→ PASS
```

---

## 11.4 Full Regression

执行 repository 当前已有、合理的完整测试集。

禁止为了追求 full test 全绿而修改无关业务代码。

---

## 11.5 Runtime Verification

涉及以下能力时必须进行必要运行态验证：

- database
- filesystem
- API
- network
- migration
- permission
- persistence
- state machine
- UI runtime

仅靠 unit test 无法证明的问题，不得以 unit test PASS 替代。

---

# 12. Verification Report

统一输出：

```text
CODEX VERIFICATION

Repository:
Branch:
HEAD:

Scope Check:
Build:
Compile:
Lint:
Type:
Target Tests:
Full Regression:
Runtime Verification:
Architecture Boundary:
Unexpected Diff:

Remaining Risks:

FINAL:
PASS / FAIL

Close Recommendation:
YES / NO
```

---

# 13. Close Criteria

只有同时满足：

```text
PLAN READY
+
MINIMALITY CHECK PASS
+
DSH DEVELOPMENT COMPLETE
+
CORRECTNESS REVIEW PASS
+
MINIMALITY REVIEW PASS
+
VERIFY PASS
```

才允许：

```text
CLOSED
```

---

# 14. Failure Routing

```text
Plan错误
→ Return to PLAN

Scope需要扩大
→ Return to PLAN

Implementation错误
→ Return to DSH

Over-engineering
→ DSH minimal remediation

Verification失败
→ 判断 Implementation / Environment

新发现但非阻断问题
→ Backlog
```

不得因为 Backlog 项无限延迟当前任务 Close。

---

# 15. Closed Issue Rule

任何已经正式标记：

```text
CLOSED
```

的事项，默认不得在其他任务中重新打开。

只有满足以下条件之一才能重新进入 Scope：

1. 出现新的可复现 Regression；
2. 新需求明确改变原 Acceptance Criteria；
3. 新证据证明原 Close 结论错误。

否则：

```text
OUT OF SCOPE
```

---

# 16. P0 Boundaries

当前版本只吸收：

```text
ECC
→ Plan / Review / Verify

Ponytail
→ Minimal Solution Check / Minimality Review
```

P0 明确不引入：

- ECC 完整安装；
- Ponytail 完整安装；
- Multi-Agent orchestration；
- 自动 Engineering Memory；
- 自动 Instinct Learning；
- 强制 Full TDD；
- Coverage 强制门槛；
- 自动 Review Fix；
- 新 CI/CD 架构。

---

# 17. Validation Period

V2 规则冻结。

至少连续运行 3 个真实开发任务，观察：

1. Scope Drift 是否下降；
2. 无必要重构是否下降；
3. 新 dependency 是否减少；
4. 重复 abstraction 是否减少；
5. Codex 是否更容易形成明确 PASS / FAIL；
6. 是否出现因为过度追求 Minimality 而遗漏 edge cases。

在完成至少 3 个真实任务前，不继续扩展 V2。

后续如进入 P1，再单独评估：

```text
Engineering Memory
```

不得直接加入当前 P0。