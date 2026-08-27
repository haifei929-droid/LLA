# LLA P2 Fixture Manifest 说明

> 对应验收要求：LLA P2 Independent Acceptance Test §2.2（fixtures 与 source-data manifest）。

## 用法

```powershell
# 生成干净验收数据库 + manifest（数据写入 data/p2_fixtures.sqlite3）
python backend/scripts/seed_p2_fixtures.py

# 仅初始化空库（验证空库状态语义）
python backend/scripts/seed_p2_fixtures.py --empty

# 自定义路径
python backend/scripts/seed_p2_fixtures.py --db data/my_fixtures.sqlite3
```

生成物：`data/p2_fixtures.sqlite3`（干净数据库）与 `data/p2_fixtures.manifest.json`（完整 manifest：每条记录的 ID、时间戳、期望分类、包含/排除规则）。**验收前请删除旧库再生成**（脚本非幂等重建）。

## 覆盖场景与期望值

### 素材与理解度（P2-1-01/03）
| 素材 | 句子数 | FIRST 档位 | 期望映射分 |
|---|---|---|---|
| fx-m1 | 3 | `<30%` | 15 |
| fx-m2 | 3 | `30–50%` | 40 |
| fx-m3 | 3 | `50–70%` | 60 |
| fx-m4 | 3 | `>70%` | 85 |

曲线每周期聚合分 = 50.0（四档平均），raw band 分布 `{<30%:1, 30–50%:1, 50–70%:1, >70%:1}`，样本数 4，映射版本 1.0。

### 听写识别会话（P2-2-01/02/03）
| 场景 | 目标 | 期望 first_correct | 期望 hint/reveal | 包含/排除 |
|---|---|---|---|---|
| 首听即对（fx-m1 s1） | lazy | 无 episode | — | **排除**（无听错证据，设计如此） |
| 多遍听出（fx-m1 s2） | seashore | 5 | — | 包含 |
| 提示后正确（fx-m1 s3） | could | **None**（从平均排除） | hint=2 | 包含（难度证据） |
| 仅 Reveal 得知（fx-m2 s1） | lazy 等句内词 | **None** | reveal=1 | 包含（难度证据，平均排除） |
| 纯拼写错误（fx-m2 s2） | market | — | — | **排除**（SPELLING 永不入难度） |
| 跨素材重复 1（fx-m3 s1） | lazy | 4 | — | 包含（跨素材聚合） |
| 跨素材重复 2（fx-m3 s2） | seashells | 4 | — | 包含（跨素材聚合） |

跨素材：`lazy` 出现在 fx-m1/fx-m2/fx-m3；`seashells` 出现在 fx-m2/fx-m3。`attempt_number` 与 `listen_count` 不同（如 seashore：提交 2 次、listen_count 5）。

### 周测与强化（P2-1-04）
- FX-W1：3 题全错 → 初始听写分 **0.0**（原始分保留显示）→ REINFORCEMENT_REQUIRED → 强化包全对 → TARGETED_RETEST → confirm → **WEEKLY_GATE_PASS**（最终 Gate 与原始分分开展示）。

### 朗读三维（P2-1-05）
fx-m4 两条练习：speed {PASS:1, FAIL:1}、pause {CLOSE:1, PASS:1}、stress {PASS:2}；speed_ratio 数值可用（95/100、120/100）。三维独立，无平均。

### Gate 与难度历史（P2-3-02/04）
- FX-G1..FX-G8 连续 8 周 PASS（90 分）→ consecutive=8、eligible=true；
- FX-FAIL（65 分）→ streak 归零（=0）。
- 事件流：WEEKLY_GATE_RECORDED / STREAK_UPDATED（8 次）+ DOWNGRADE 触发需先确认 stage>STAGE_1。

### 空库变体
`--empty` 生成仅含 schema 的库：仪表盘 hours=0（语义正确）、曲线无样本、Memory 返回 UNCONFIGURED（未配置阈值）、缺失显示 missing_reason 而非伪造 0/FAIL。

## 复核方式（独立 reviewer 可重算，无需读实现代码）

```text
1. 听写分：SELECT dictation_score FROM weekly_assessments WHERE week_id='FX-W1' → 0.0
2. first_correct：查 fx-m1-sentence-002 的 dictation_attempts，
   exact 提交（listen_count=5、hint=0、revealed=0）→ first=5
3. 拼写排除：fx-m2-sentence-002 中 expected='market' 的错误条目 error_type=SPELLING
   → 不得出现在 /api/p2/memory 的目标列表
4. 曲线：4 个 FIRST 原始档 → 15/40/60/85，聚合 50.0，样本 4
5. 时长：INSERT 已知秒数的闭日志后核对 /api/p2/dashboard summary
```
