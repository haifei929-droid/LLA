# 素材池 + 自建难度分级（Plan Gate · 第三步）

- 日期：2026-11-18
- 基线：`c3e1044`（clean，`origin/master` 同步）
- 决策来源（用户已确认）：
  1. **接受第三档形态**：人工获取 + 本地导入，不做自动抓取；
  2. **接受路线 3**：改为"素材池 + 自建难度分级"；
  3. **硬约束：只收英音（en-GB）**。
- 状态：**PLAN ONLY**，未改任何生产代码；待批准后进入 Implementation。

## 0. 一句话说明这个方案在解决什么

原来的治理模型假设"一个平台、一条课程线、按平台顺序走"。现在改成：**素材来源可以多，但入口只有一个（本地导入），难度不靠平台自报，而是自己量出来**。英音是硬门槛，不是偏好。

## 1. 与旧模型的关系（不是推倒重来）

| 维度 | 旧模型（平台主源） | 新模型（素材池） |
|---|---|---|
| 来源 | 单一 Active Source，平台内课程顺序 | 多来源入池，来源只作溯源与"延续性"权重 |
| 顺序 | 平台 `course_order + item_order` | 保留字段做溯源；选材改由难度分 + 池内档位决定 |
| 难度 | 平台自报 CEFR | **自建**：语速 / 句长 / 句间停顿 → 综合分 → 档位 |
| 准入 | 授权 + 口音 + 稿音核验 | **不变**，仍然是硬门槛 |
| 获取 | 程序化抓取（现已证伪） | 人工下载 → `data/import/inbox/` → 本地导入 |

保留不动的：`accent` 白名单、`license_status`、`transcript_verified`、`ingestion_status`、去重身份索引、单写事务、失败可重试、训练状态机与八周升级机制。

## 2. 池的数据模型（复用为主，新增 2 列）

现有 `materials` 已经具备：`source_platform / source_course / item_order / source_url / accent / cefr_level / transcript_verified / license_status / ingestion_status / speech_rate_wpm / duration_seconds / difficulty_level / speed_stage`，`sentences` 已存逐句 `text + start_time`。

只需新增（沿用现有 `ALTER TABLE ... ADD COLUMN` 迁移风格）：

| 新列 | 类型 | 含义 |
|---|---|---|
| `difficulty_score` | REAL | 自建综合难度分（0–100） |
| `difficulty_evidence` | TEXT (JSON) | 打分明细：wpm、平均句长、90 分位句长、平均句间停顿、标定版本 |

`cefr_level` 改为**由我们写入**（依据难度分映射 + 人工标定），不再照抄平台声明；平台声明若存在，存到 `difficulty_evidence` 的 `platform_claim` 里留痕。

## 3. 自建难度分级（不引入新依赖）

只用仓库里已有的数据（文字稿 + 逐句时间戳）算三个可测量指标：

1. **句间停顿**：`gap_i = start_{i+1} - end_i`，取中位数与 90 分位；
2. **语速**：`wpm = 词数 / (总时长 / 60)`；
3. **句长**：平均词/句、90 分位句长。

综合分（权重先用等权，标定后再调）：

```
score = 100 * (0.45 * norm(wpm) + 0.30 * norm(mean_len) + 0.25 * norm(gap_p50))
```

档位映射（初版阈值，**必须用标定样本重估**）：

| 分数区间 | 档位 |
|---|---|
| 0–33 | A1 |
| 33–58 | A2 |
| 58–80 | B1 |
| 80–100 | B2（本阶段不投放） |

**标定步骤（人工，必须做）**：从池里抽 12–20 条，人工按"能不能给 A1 学员听写"打 1–5 分，用这批样本回归修正权重与阈值，把版本号写进 `difficulty_evidence.calibration`。没有标定的阈值不得用于生产选材。

## 4. 选材算法（替代 course_order）

```
候选 = 池中满足全部硬门槛的行
硬门槛 = accent == 'en-GB'
        AND license_status == 'CLEARED'
        AND transcript_verified == 1
        AND ingestion_status == 'INGESTED'
        AND prepare_status == 'READY'
        AND difficulty_band ∈ [当前档, 当前档+0]   # 八周确认前不跨档
排序 = (同来源延续优先) → difficulty_score 升序 → item_order 升序 → material_id
```

- "同来源延续"：若当前正在训练的素材来自 `source_platform + source_course`，同来源同课程优先（保留连续性原则）。
- 池空时**显式报错**（`POOL_EMPTY`），不得回退到随机或降门槛。
- 八周升级仍由现有 `difficulty_progression` + `upgrade_prompts` 决定，`difficulty_band` 只是执行层。

## 5. 导入通道（人工，唯一入口）

1. 用户把官方包解到 `data/import/inbox/<batch>/`（音频 + 文字稿 + 可选元数据 JSON）；
2. 运行 `scripts/import_local.py --batch <batch>`：
   - 解析音频与文字稿，按句切分并对齐时间轴（复用 `align_sentences`）；
   - 写入 `materials`（`ingestion_status='QUARANTINED'` 起步）+ `sentences`；
   - 计算 `difficulty_score` 与证据；
   - 同一 `source_platform + source_course + item_order` 重复导入 → 幂等跳过；
3. 人工复核口音与稿音一致性 → 置 `accent='en-GB'`、`transcript_verified=1`、`license_status`、`ingestion_status='INGESTED'`。

**不做的事**：不写任何自动下载/抓取；不猜口音；不把未复核素材放进可训练池。

## 6. 采集合规边界（必须与池方案一起读）

| 素材 | 音频 | 文字稿 | 结论 |
|---|---|---|---|
| 50languages `ENEN.ZIP`（English UK，100 课） | CC BY-NC-ND 3.0 US（可非商业分享、须署名、**不得修改**） | **All rights reserved** | 仅限个人本地使用；切句属"可能改编"，灰区；**不得分发**；文字稿入库展示无授权依据 |
| 开放许可素材（CC BY-NC-SA / CC BY / 公共领域） | 允许改编（SA 视条款） | 视条款 | 池的**推荐主来源**；需逐条确认许可 |

因此池的来源策略是：**优先开放许可，50languages 仅作"个人本地、不分发"的特例**，并且这一限制要写进每条素材的 `license_status` 与界面提示。

## 7. 验收测试（10 条，Implementation 阶段必须全绿）

1. `en-US` 素材：手工导入被拒、池查询不返回；
2. 口音未知：入库即 `QUARANTINED`，不参与选材；
3. `license_status != 'CLEARED'`：不可选；
4. `transcript_verified = 0`：不进逐字判分池，也不进周测池；
5. 难度单调：当前档 A1 时不会选出 A2；
6. 八周未确认：档位不上移（含客户端 `speed_stage` 参数不得越级）；
7. 重复导入幂等：同一来源身份不产生第二条素材；
8. 池空显式报错 `POOL_EMPTY`，不回退随机；
9. 训练状态机回归：听写/朗读/周测/时长/跨天不变（既有 169 条测试基线不退化）；
10. 导入中断可续、失败可重试、无半成品素材。

## 8. 最小实施范围（待批准）

| 文件 | 变更 |
|---|---|
| `backend/app/db/schema.sql`、`connection.py` | 新增 2 列 + 迁移（与既有治理列同风格） |
| `backend/app/core/difficulty_scoring.py`（新） | 纯函数：指标 → 分数 → 档位；无 IO |
| `backend/app/core/material_pool.py`（新） | 池查询 + 选材；复用现有 gate 字段 |
| `scripts/import_local.py`（新） | 人工批次导入 + 幂等 + 难度打分 |
| `backend/app/api/routes.py` | 池状态/池查询/导入触发（或复用现有 `POST /materials`） |
| `backend/tests/test_material_pool.py`（新）+ 适配 | 覆盖 §7 |
| `docs/` | 本计划 + 标定记录模板 |

不引入：爬虫框架、新数据库、后台管理系统、新第三方依赖。

## 9. 前置依赖（必须一起解决，否则方案无法上线）

**周测新题池目前是坏的。** `test_quarantined_material_is_isolated_from_the_new_weekly_pool` 已被你决定放弃修复，但池一上线就会触发：

```
ValueError: No verified material matches the active training source; the weekly test pool is empty
```

也就是说：**素材池上线的那一天，周测立刻不可用**。顺序上只有两个选择：
- (a) 在实施素材池的同一次交付里修掉周测池（推荐，属同一闭环）；
- (b) 素材池先只做导入与选材，明确关闭周测入口，直至修复。

这一点必须在批准实施时一并决定。

## 10. 未决项

1. 标定样本由谁提供（需要 12–20 条人工难度评分，最好由你听一遍）；
2. 池是否允许"同一课程多来源混排"，还是保持"一次只推进一门课程"（影响连续性权重）；
3. B2 及以上的素材是否一律拒收（当前映射表把 B2 设为不投放）；
4. 50languages 的"个人本地特例"是否进入正式池，还是单列为"非分发实验区"。