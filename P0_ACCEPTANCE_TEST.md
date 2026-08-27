# LLA P0 Final Acceptance Test

**Project:** Language Learning Agent / LLA  
**Acceptance Target:** P0  
**Primary Spec:** `docs/Language_Training_Agent_P0_Development_Spec_V1.0.docx`

## 1. Purpose

本文件定义 LLA P0 的最终验收标准。

Codex 不得依据以下条件单独判断 P0 已完成：

- 功能代码已编写；
- 页面可以打开；
- 单元测试通过；
- Build 成功；
- Mock 数据链路可以运行。

P0 必须同时完成：

1. 自动化测试；
2. 真实素材 End-to-End 运行态测试；
3. 状态恢复测试；
4. 数据正确性检查；
5. 周测与强化训练闭环；
6. 朗读评分可用性验证；
7. 学习时长审计。

最终只能输出以下三个结论之一：

- `P0 ACCEPTED`
- `P0 ACCEPTED WITH KNOWN LIMITATIONS`
- `P0 NOT ACCEPTED`

---

# 2. Acceptance Principles

## 2.1 Training Core is authoritative

训练状态、PASS/FAIL、Weekly Gate、训练进度不得依赖 LLM 自由判断。

LLM 只负责：

- 内容理解分析；
- 错误解释；
- 周测内容生成；
- 强化训练生成；
- 自然语言反馈。

## 2.2 Test from clean state

正式验收必须至少执行一次：

```text
Empty / clean SQLite database
↓
Load preset material
↓
Run complete training lifecycle
↓
Weekly test
↓
Reinforcement
↓
Retest
```

不得仅使用开发过程中已经存在的数据判断通过。

## 2.3 Evidence required

每个验收项必须提供：

- 测试方法；
- 实际结果；
- PASS / FAIL；
- 可验证证据。

不得只输出“测试已通过”。

---

# 3. Automated Test Gate

Codex 首先执行项目当前全部自动测试。

至少覆盖：

## Training State Machine

验证：

- 合法状态转换；
- 非法状态转换被拒绝；
- Dictation Part 1 → Part 2 → Part 3；
- Reading 解锁条件；
- Full Reading Assessment 解锁条件；
- Material Listening Completed；
- Material Fully Completed；
- Weekly Gate 状态变化。

## Dictation

验证：

- 第1遍只听；
- 第2遍允许输入；
- 第3遍修改；
- Hint；
- Reveal；
- `____` 空缺占位；
- 逐字正确 PASS；
- contraction / expanded form 等价。

例如：

```text
would've == would have
I'm == I am
don't == do not
```

## Dictation Error Classification

覆盖：

```text
MISS
MISHEARD
WORD_FORM
SPELLING
```

特别验证：

> `SPELLING` 不得写入 Listening Memory 作为听力弱点。

## Listening Memory

验证：

- encounter count；
- first correct attempt；
- hint count；
- reveal count；
- 跨素材累计；
- 高频困难项识别。

## Reading

验证：

- speed 独立判断；
- pause 独立判断；
- stress 独立判断；
- 三项不得简单平均后覆盖 FAIL。

## Weekly Gate

验证：

```text
Dictation >= 80%
→ PASS

Dictation < 80%
→ REINFORCEMENT_REQUIRED
```

如果本周没有进行 Reading：

> Weekly Assessment 不得生成 Reading Test。

如果本周进行过 Reading：

> 必须生成 Reading Test。

## Reinforcement

验证：

```text
FAIL
→ Reinforcement
→ Targeted Retest
→ PASS
```

## Learning Timer

验证：

- 有效训练计时；
- pause 不计时；
- idle 不计时；
- LLM 等待不计时；
- 重启后不重复累计。

## Engineering Checks

执行项目已有的：

```text
backend tests
frontend tests
lint
typecheck
build
```

任何 Blocking Failure 均不得进入 `P0 ACCEPTED`。

---

# 4. Real Material E2E Acceptance

使用一篇真实预置英语素材。

推荐长度：

```text
15–20 minutes
```

禁止仅使用 Mock transcript / Mock audio 完成正式验收。

完整运行：

```text
Preset Material
↓
First Full Blind Listen
↓
First Comprehension Rating
↓
First Summary
↓
Dictation Part 1
↓
Dictation Part 2
↓
Dictation Part 3
↓
Second Full Listen
↓
Second Comprehension Rating
↓
Second Summary
↓
Reading Part 1
↓
Reading Part 2
↓
Reading Part 3
↓
Full Reading Assessment
↓
Material Fully Completed
```

必须证明整条链路无需：

- 手工修改数据库；
- 手工跳状态；
- 修改代码；
- 使用开发后门。

---

# 5. Dictation Runtime Acceptance

在真实 E2E 中人工构造至少以下情况：

1. 漏听一个词；
2. 听错一个词；
3. 词形错误；
4. 拼写错误；
5. 使用 `____`；
6. Hint 后正确；
7. Reveal 后才知道答案。

验收完成后直接检查 SQLite 数据。

必须确认：

- Attempt Number 正确；
- First Correct Attempt 正确；
- Hint 使用正确；
- Reveal 正确；
- Error Type 正确；
- Listening Memory 正确；
- Spelling Error 未污染 Listening Memory。

---

# 6. Crash / Resume Acceptance

必须执行真实中断恢复。

## Dictation

停在：

```text
Part 2
Sentence 16
Attempt 3
```

直接关闭服务或程序。

重新启动后必须准确恢复：

```text
Part 2
Sentence 16
Attempt 3
```

不得只恢复到 Part 2。

## Reading

同样测试：

```text
Reading Part 2
Round 3
```

重新启动后必须恢复正确训练位置。

本项失败：

> P0 不得 ACCEPT。

---

# 7. Reading Assessment Validation

朗读评分是 P0 高风险模块，必须进行对照录音测试。

针对同一 Reference Audio，至少准备：

## Sample A — Normal

- 接近原语速；
- 正常停顿；
- 正常强弱节奏。

## Sample B — Intentionally Slow

明显低于原语速。

系统必须识别：

```text
Speed degradation
```

## Sample C — Bad Pause / Flat Stress

故意：

- 在错误位置停顿；
- 频繁额外停顿；
- 全句平均用力；
- 缺乏明显重音。

系统应该能够方向正确地识别：

```text
Pause degradation
Stress degradation
```

### Stability Test

同一录音连续评估至少 3 次。

结果必须基本稳定。

不要求 P0 达到语言学实验室精度，但不得出现：

> 相同录音一次 PASS、一次明显 FAIL 的严重漂移。

---

# 8. Weekly Assessment Acceptance

模拟一周训练数据，无需真实等待七天。

## Scenario A

```text
Dictation Score = 86%
```

期望：

```text
Dictation PASS
Weekly Gate PASS
```

## Scenario B

```text
Dictation Score = 72%
```

期望：

```text
Dictation FAIL
Weekly Gate = REINFORCEMENT_REQUIRED
```

此时首页：

不得主动推荐：

```text
Next Material
```

应该优先推荐：

```text
Reinforcement Training
```

---

# 9. Weekly Dictation Generation

周测听写内容必须：

- 基于当周素材；
- 使用当周出现过的词汇；
- 使用当周学习过的结构；
- 重新拆解组合成长句；
- 不直接复制全部原句；
- 不引入当周范围外的新生词。

必须检查生成结果。

如出现明显超出当周范围的新词：

> Test Generation FAIL。

---

# 10. Weekly Reading Test

## User without reading training

如果本周没有任何 Reading Attempt：

```text
Reading Test = NOT REQUIRED
```

不得强制生成。

## User with reading training

必须生成 Reading Test。

评估：

```text
Speed
Pause
Stress
```

例如：

```text
Speed PASS
Pause PASS
Stress FAIL
```

结果必须：

```text
Reading FAIL
Weekly Gate != PASS
```

不得使用平均总分抵消单项 FAIL。

---

# 11. Reinforcement Acceptance

Weekly Gate FAIL 后：

```text
REINFORCEMENT_REQUIRED
↓
Generate Reinforcement Package
```

强化包必须：

- 只针对失败能力；
- 使用当周语言范围；
- 不引入新词；
- 不重新学习整周；
- 训练量约为原周测的 30%–50%。

完成后：

```text
TARGETED_RETEST
```

只测试失败能力。

通过后：

```text
WEEKLY_GATE_PASS
```

首页恢复允许推荐下一轮学习。

---

# 12. Learning Time Audit

执行可验证的计时测试。

示例：

```text
Active training     5 min
Pause               3 min
Active training     4 min
LLM waiting         1 min
```

期望有效训练时间：

```text
≈ 9 min
```

而不是：

```text
13 min
```

同时验证：

- Session Learning Time；
- Weekly Learning Time；
- Total Learning Time。

关闭程序并重新启动后：

- 不丢失；
- 不重复；
- 不倒退。

---

# 13. UI Acceptance

P0 UI 不要求高级视觉效果，但必须符合：

> Personal Training Dashboard

而不是传统英语学习 App。

检查：

- 色块承担主要状态表达；
- 图形简单；
- 信息层级清晰；
- 无无关游戏化；
- 用户打开首页可以快速知道下一步做什么。

## Dictation UI

必须：

- 错误具有明显颜色区分；
- 支持 `____` 空缺；
- 留空不会卡死流程；
- 错误反馈不直接过早 Reveal 全部答案。

## Reading UI

必须：

- 原文为主要视觉信息；
- Shadowing 过程不实时打断；
- Part 完成后统一反馈；
- Speed / Pause / Stress 清晰分开。

---

# 14. Adapter Failure Recovery

至少测试：

## LLM unavailable

LLM API 暂时不可用时：

- 已有训练进度不得丢失；
- SQLite 不得损坏；
- 可以安全重试；
- 不得错误跳状态。

## Speech service unavailable

本地 ASR / Speech Adapter 失败时：

- Reading Attempt 不得错误标记 PASS；
- 用户录音不得无提示丢失；
- 应允许重新分析或重新训练。

---

# 15. Final Acceptance Matrix

Codex 最终必须输出如下矩阵。

| Acceptance Area | Automated | Runtime | Result | Evidence |
|---|---|---|---|---|
| Training State Machine | | | | |
| Real Material E2E | | | | |
| Dictation Exact Match | | | | |
| Dictation Error Types | | | | |
| Listening Memory | | | | |
| Crash / Resume | | | | |
| Reading Speed | | | | |
| Reading Pause | | | | |
| Reading Stress | | | | |
| Reading Stability | | | | |
| Weekly Dictation | | | | |
| Conditional Reading Test | | | | |
| Weekly Gate | | | | |
| Reinforcement | | | | |
| Targeted Retest | | | | |
| Learning Timer | | | | |
| UI Main Flow | | | | |
| Adapter Failure Recovery | | | | |
| Build / Lint / Typecheck | | | | |

---

# 16. Final Decision Rules

## P0 ACCEPTED

仅当：

- 所有核心主链路通过；
- 无 Blocking Failure；
- 真实 E2E 通过；
- Crash / Resume 通过；
- Weekly Gate 通过；
- Reinforcement 闭环通过；
- Learning Time 数据可信；
- Reading Assessment 达到稳定可用。

## P0 ACCEPTED WITH KNOWN LIMITATIONS

仅用于：

- 核心训练闭环全部正确；
- 存在明确、不影响主链路的非阻塞限制。

必须逐项列出：

```text
Known Limitation
Impact
Workaround
Recommended P1 Action
```

不得用此状态掩盖核心功能失败。

## P0 NOT ACCEPTED

以下任意一项失败，应优先判定：

- 主训练链路无法完成；
- Dictation 数据错误；
- Listening Memory 不可信；
- Crash / Resume 丢状态；
- Weekly Gate 失效；
- Reinforcement 无法闭环；
- Timer 明显错误；
- Reading Assessment 无法产生稳定、方向正确的结果。

---

# 17. Required Final Report

Codex 完成测试后必须输出：

## A. Environment

```text
OS
Python version
Node version
Database
Speech model/provider
LLM provider/model
Preset material
```

## B. Commands Executed

列出所有实际执行命令。

## C. Automated Test Results

列出：

```text
Passed
Failed
Skipped
```

## D. Runtime Acceptance Matrix

填写第15节完整矩阵。

## E. Known Limitations

如果没有：

```text
None
```

## F. Final Conclusion

最终只允许：

```text
P0 ACCEPTED
```

或：

```text
P0 ACCEPTED WITH KNOWN LIMITATIONS
```

或：

```text
P0 NOT ACCEPTED
```

并给出结论依据。

---

# 18. Codex Execution Instruction

Codex：

请基于：

```text
Language_Training_Agent_P0_Development_Spec_V1.0.docx
P0_ACCEPTANCE_TEST.md
```

对当前 LLA 项目执行完整 P0 最终验收。

不要依据代码实现状态判断完成。

必须：

1. 从 clean database 开始；
2. 执行完整 automated tests；
3. 执行真实 preset material E2E；
4. 执行 crash / resume；
5. 检查真实 SQLite 数据；
6. 执行 Reading A/B/C 对照测试；
7. 模拟 Weekly Assessment；
8. 执行 Reinforcement + Targeted Retest；
9. 审计 Learning Timer；
10. 输出完整 Acceptance Matrix。

如果当前环境缺失完成某项验收所需的真实依赖或素材：

- 不得伪造 PASS；
- 明确标记 `NOT VERIFIED`；
- 说明缺失条件；
- 根据是否属于 Blocking Item 决定最终 P0 状态。

禁止把：

```text
unit tests pass
build success
mock test pass
```

单独作为 `P0 ACCEPTED` 的依据。