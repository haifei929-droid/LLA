# LLA 主平台授权评估（Plan Gate · 第二步）

- 评估日期：2026-11-18
- 评估基线：`42c6645`（`git status` clean，`origin/master` 已同步）
- 评估范围：用户原定优先级 1–4 的四家来源
- 方法：**逐条抓取官方授权条款与 robots 级约束（第一手）**；不下载任何音频、不枚举 mp3 清单、不做批量探测
- 结论状态：**不具备启动正式采集的授权条件**（不是"还没评估完"，是"评估完了，四家都不满足"）

> 阅读提示：本文所有英文引用均逐字取自下列 URL 的当前版本（2026-11-18 抓取）。两家站点对自动化请求返回 403，相应条目改用 Wayback 快照并已标注，不臆测原文。

## 0. 结论摘要

| 平台 | 授权能否覆盖"本地存储 + 非商业自用" | 能否覆盖"按句切分 + 时间轴对齐"（衍生） | 能否合法"程序化批量获取" | 判定 |
|---|---|---|---|---|
| 50languages / book2 | 音频可以（CC BY-NC-ND，须署名），**文字稿不行（全部版权保留）** | **不行**（ND 禁止修改） | **不行**（条款明文禁止自动化批量获取） | 不可自动采集；需书面授权，且要单独豁免 ND 与文字版权 |
| British Council LearnEnglish | 可以（明确鼓励下载，须署名） | **不行**（"modify in any way"） | **不行**（自动请求一律 403） | 排除（除非书面授权） |
| Breaking News English | 不行（只授权纸质打印自习） | **不行**（禁止复制片段造新版本） | 未授权（mp3 明确排除在链接许可外） | 排除（条款点名禁止 app） |
| BBC Learning English | 弱允许（归档条款：个人/教育非商业） | 灰区（同一句条款既禁"edit/adapt"又给"个人使用"例外） | **不行**（robots 明文反对 scraping / systematic extraction） | 仅可作补充，且需书面确认 |

**没有一个平台可以"装上适配器就开始跑"。** 这直接改变了原定的接入顺序：原计划的"先 50languages 再 BC 再 BNE 再 BBC"在授权维度上是反的——授权面最宽的是 BBC，最窄的是 BNE，而 50languages 的"音频 CC"并不能救它的文字版权与自动化禁令。

## 1. 判定标准（来自原始需求，三条必须同时成立）

1. **授权**：允许把音频与文字下载到本地，用于个人、非商业的英语训练；
2. **衍生**：允许按句切分音频、对齐时间轴、生成听写判分依据（这构成改编）；
3. **获取**：允许稳定、合法的程序化获取，且不绕过登录/付费/访问控制/反爬。

任何一条不成立，该平台就不能成为"Active Source"。

## 2. 逐平台评估

### 2.1 50languages / book2（Goethe-Verlag）

条款出处：[Terms of Use（2026-08-21 版）](https://www.goethe-verlag.com/agb.php)、[音频许可页](https://50languages.com/licence)、[robots.txt](https://50languages.com/robots.txt)

**§4 内容权利**（第一手）：

> "The learning content, in particular the book2 texts and the accompanying audio recordings, is © Goethe Verlag GmbH, Starnberg, Germany. All rights reserved."
> "Except as permitted by mandatory law or expressly allowed in section 5, the content of this website may not be copied, distributed, modified or made available to third parties, and it may not be used for commercial purposes."

**§5 音频许可**（第一手）：

> "The audio files of the book2 series are published under the Creative Commons licence **Attribution-NonCommercial-NoDerivs 3.0 (CC BY-NC-ND 3.0)**. Under that licence you may share the files for non-commercial purposes, provided you credit the rights holder and do not modify the files."
> "The licence covers the book2 audio files only. It does not extend to the images, the layout or to any other content of this website."

**§6 可接受使用**（第一手，决定性）：

> "retrieve content in bulk by automated means, mirror the website, or place systematic load on it (scraping, crawling beyond ordinary search-engine indexing, download tools);"

**三条冲突（逐条对应原始需求）**：

1. **文字稿没有许可**。§5 只覆盖音频，明确"不延及本站其他任何内容"；§4 对文字是"All rights reserved"。LLA 的逐字听写判分**必须**以官方文字稿为准，这是本平台最硬的一堵墙。
2. **ND 禁止改编**。切句、重排、打时间轴都是"modify the files"。音频即便拿到 CC，也不能切。
3. **自动化获取被明令禁止**。§6 点名 "download tools" 与 "bulk by automated means"——这正是 LLA 采集器的形态。**注意：CC 许可允许你分享文件，不等于允许你绕过站点条款去批量抓取；获取方式与使用许可是两层。**

**判定：不可作为程序化采集主平台。** 如要坚持，必须同时拿到（a）文字内容的复制与改编许可、（b）ND 的单独豁免、（c）自动化获取的书面许可——三件事写在同一封回信里才算数。

### 2.2 British Council LearnEnglish

说明：`learnenglish.britishcouncil.org` 与 `www.britishcouncil.org` 对本环境的自动请求**全部返回 403**（Akamai，`errors.edgesuite.net`）。条款原文取自 [Wayback 快照（2026-01-01）](https://web.archive.org/web/20260101100312/https://www.britishcouncil.org/terms)，标注为二手复核。

**§10**（快照）：

> "We encourage you to access, copy, re-tweet, share, download and print, view and/or listen to British Council Content for your own personal and non-commercial use, provided you credit the British Council and abide by any notice or restrictions in the material…"
> "You must not use British Council Content … for any other purpose without our prior written approval, including: … **modify British Council Content in any way**; use any illustrations, images, video or audio sequences or any graphics within British Council Content separately from any accompanying text;"

**§14**（快照）：

> "you may not copy any part of British Council Digital Services into different websites or products without our prior written permission"

**冲突**：

1. 下载本身被鼓励（站方甚至说明"可右键另存音频"），署名要求清晰——**这一维度是四家里最好的**；
2. 但"modify in any way"直接封死切句与时间轴对齐；
3. "复制进 other products"封死嵌入本地应用；
4. 工程侧：所有自动请求 403，程序化获取在技术上就被拒绝了。

**判定：排除。** 人工浏览/收听可以，作为 LLA 素材来源不行。

### 2.3 Breaking News English（Sean Banville）

条款出处：[copyright.html](https://breakingnewsenglish.com/copyright.html)（第一手，全站唯一条款页）、[robots.txt](https://breakingnewsenglish.com/robots.txt)（`User-agent: *` / `Disallow:` 为空，即不限制抓取）

**关键原文**（第一手）：

> "**You are given permission...** to print out and duplicate paper copies of these lessons to be used by your own students or for private study purposes by students."
> "to link directly to the html files on my site (but **not** the mp3 files)."
> "Any other use of my files and mp3s requires written permission."
> "Permission is **not** granted to reproduce the Article, the exercises that go with it, or any material from this website on any other website, social media platform, online video sharing platform, blog, **app**, Learning Management System (LMS) or Course Management System (CMS) in any form."
> "Permission is **not** granted to copy and paste sections of the materials to create different versions or formats of the materials."

**判定：明确排除。** 条款逐字点名 "app"，且不区分商用/非商用、单用户/多用户。robots 允许抓取 ≠ 允许使用。要落地必须拿 Sean Banville 的书面许可（`info@breakingnewsenglish.com`），并在许可里写明：本地副本、句子切分、与音频对齐、生成练习、单机单用户、不再分发。

### 2.4 BBC Learning English

条款出处：[LE Terms of use](https://www.bbc.co.uk/worldservice/learningenglish/terms.shtml)（页面自标 "archived and no longer updated"）、[LE Copyright](https://www.bbc.co.uk/worldservice/learningenglish/copyright.shtml)（同样已归档）、[BBC robots.txt](https://www.bbc.co.uk/robots.txt)（现行）

**归档条款（第一手）**：

> "You may not copy, reproduce, edit, adapt, alter, republish, post, broadcast, transmit, make available to the public, or otherwise use audio, video or other material downloaded from BBC Learning English in any way **except for your own personal or educational non-commercial use**."
> "you may only download the content for your own personal non-commercial use… You are not permitted to … store (in any medium), … adapt or change in any way the content … for any other purpose whatsoever without the prior written permission of the BBC."

**现行 robots.txt（第一手，决定性）**：

> "In short: Please use our site like a human, not a robot. That means: - **No scraping, crawling, or systematic extraction of content** … - **No creating datasets from BBC content**"

**冲突**：

1. 使用面最宽：个人/教育非商业用途，连 "edit, adapt, alter" 都写进了"except for"的例外里——理论上支持按句切分；
2. 但**该页已归档**，现行有效的是 BBC 全球条款（禁止把内容用于非 BBC 的产品/服务）；
3. robots.txt 用明文反对 scraping / systematic extraction / 建数据集——**这是"获取方式"的拒绝，不是"使用范围"的许可**；
4. 工程侧：mp3 直链存在且稳定（`downloads.bbc.co.uk/learningenglish/features/6min/<YYMMDD>_6min_english_<slug>_download.mp3`），podcast 仅保留 30 天，网站档案页更久。

**判定：仅可作补充来源，且需书面确认。** 不能作为自动化主平台。

## 3. 冲突矩阵（三条必要条件 × 四家）

| | 本地存储 + 非商业自用 | 切分/对齐（衍生） | 程序化获取 | 结论 |
|---|---|---|---|---|
| 50languages | 音频 ✅ / 文字 ❌ | ❌（ND） | ❌（§6） | 3 项不成立 |
| British Council | ✅（须署名） | ❌（modify） | ❌（403） | 2 项不成立 |
| BNE | ❌（仅纸质） | ❌ | ❌（mp3 未授权） | 3 项不成立 |
| BBC LE | ⚠️（归档条款，须确认） | ⚠️（灰区） | ❌（robots） | 1 项不成立 + 2 项不确定 |

**这张表就是本次 Plan Gate 的全部产出：没有 Active Source 可以激活。**

## 4. 与授权无关、但同样决定可行性的事实（必须一起看）

| 事实 | 影响 | 证据 |
|---|---|---|
| BBC 6 Minute English 官方标注 **"Note: This is not a word-for-word transcript."** | LLA 的判分标准是"逐字完全正确"。用非逐字稿当标准答案，会把官方省略/改写判成用户错误——**产品正确性硬伤** | 6ME 集页（如 ep-220609） |
| 6ME 会插入 BBC Radio 4 访谈片段，嘉宾可能非英音 | 与"发音一致性"直接冲突；不能只凭"BBC 出品"推断全英音 | 同上集页 |
| 50languages "Most languages have male and female voices combined in one MP3 file" | 男女声混读，同一课内音色跳变 | 官方 MP3 说明页 |
| BC 的 A1 听力含格拉斯哥/伦敦/曼彻斯特等地域口音；B2/C1 明确"speakers are from all over the world" | 需要逐条验口音，不能整站默认 en-GB | A1 PDF / Audio zone |
| BNE 英音与北美音**分文件**，但只有第 3、4 号播放器标注口音，RSS 里的 `{slug}.mp3` 未标 | 工程上必须解析 `<audio><source src>` 而非拼文件名；否则会混入美音 | BNE `-l.html` |

结论：即便授权全部解决，**"逐字稿 + 纯英音 + 同级难度"在当前四家公开内容里也只能靠逐条人工核验得到**，不能靠平台品牌推断。

## 5. 推荐方案（三档，按风险从低到高）

### 5.1 第一档（推荐先做）：书面授权询问，不采集
不写采集器，先写信。三家联系入口：

- 50LANGUAGES LLC / Goethe Verlag：`info@goethe-verlag.com`
- BBC Learning English：`learningenglish@bbc.co.uk`
- BNE（如仍考虑）：`info@breakingnewsenglish.com`

**询问模板（要点，三段话即可）**：

```
Subject: Permission request: local, single-user offline use of <platform> audio + transcripts

I am building a private, single-user, offline English training tool on my own
computer (no distribution, no public availability, no charge, no AI training).
For personal study it stores <platform> audio and the official transcript
locally, splits the audio at sentence boundaries, aligns each sentence to its
text, and uses that for dictation practice.

I would like written confirmation on three points:
1) storing the audio and the transcript locally for personal, non-commercial use;
2) creating sentence-level excerpts and aligning timestamps (a modification /
   adaptation of the files);
3) retrieving the files programmatically for my own use only, at a low rate
   (clearly stating volume: e.g. 3 lessons now, at most N per week later),
   without bypassing any access control.

If any of these require a different licence or are not permitted, please tell me
what is allowed instead.
```

要点：**必须在信里写清"单用户、本地、不分发、不收费、不做 AI 训练"，并把切分与自动化单独列出**——这三件事恰好是各家条款最容易踩的点。

### 5.2 第二档：改用"真正允许衍生"的开放许可来源（需下一轮评估）
以下候选**尚未验证**，只登记为下一轮 Plan Gate 的对象，不得据此接入：

- **audio-lingua**（法国凡尔赛学区项目，`audio-lingua.ac-versailles.fr`）：录音由母语者提供、发布在 **CC BY-NC-SA** 下（允许非商业改编，衍生须同协议）；单条时长 ≤2 分钟。**注意**：`audio-lingua.eu` 域名现已指向赌博推广页，只有 `.ac-versailles.fr` 是原站；本次抓取该站失败（连接失败），可达性与"是否提供逐字文字稿"必须先验证。
- **LibriVox / Project Gutenberg**：公共领域音频 + 公共领域文本，授权最干净；代价是无 A1–B1 分级课程、材料是文学作品，与"课程顺序 + 难度渐进"不匹配。
- **OpenLearn（The Open University）**：部分内容 CC BY-NC-SA，需逐页确认标注；英音资源多但非分级课程。

若走这条，需重写"平台内课程顺序"的实现前提：现在代码假设 `course_order + item_order` 的目录结构，开放素材通常只有"按主题/按日期"的松散组织。

### 5.3 第三档：改变形态为"人工获取 + 本地导入"
用户自己用浏览器合法下载（BC 官方就说"右键另存"是允许的），LLA 只做**本地导入、切分、对齐、判分**，不实现任何自动抓取。这样把"获取"的合规责任放回用户，规避 §6/robots 的自动化条款。

代价：批量效率低（每课需人工存 2 个文件）、无法自动做"平台完成度报告"（目录快照得人工维护），且 BC/50languages 的"禁止修改/禁止复制进产品"仍然存在——**这条路只能救"获取方式"，救不了"改编"**。

### 5.4 明确排除
- **BNE**：条款点名 "app"，无论个人与否都不行。
- **British Council 直接接入**：`modify in any way` + 自动请求 403。
- **50languages 自动采集**：§6 明文禁止 download tools。

## 6. 授权通过后的"小规模验证"方案（≤3 条，先不批量）

前提：拿到书面许可后，才执行本节。验收清单（每条素材逐项记录，写入 `materials` 治理列）：

| 验收项 | 判定方法 | 记录字段 |
|---|---|---|
| 口音为英式 | 人工听辨 + 说话人背景核对，不凭平台品牌 | `accent='en-GB'`、`speaker_id` |
| 文字稿逐字可信 | 抽样 3 句人工校对；非逐字稿必须标 `transcript_verified=0` | `transcript_verified` |
| 音质 | 无背景音乐、无显著噪声、采样率 ≥16k | `audio_quality_status` |
| 速度与难度 | 时长、词/分钟、CEFR 初判 | `cefr_level`、`speech_rate`、`audio_duration` |
| 授权与出处 | 许可类型、链接、抓取日期 | `license_status`、`source_url`、`imported_at` |
| 顺序 | 平台原始课程顺序（不重排） | `source_course`、`source_item_id` |

## 7. 与现有代码的衔接（现状是安全的）

- `main.py` 现在**不注册任何素材 provider**，正是本评估的默认正确状态：没有授权就没有 Active Source，`/api/p1/material-candidates/search` 与 `/api/materials/next` 会返回 `SOURCE_NOT_APPROVED`。
- 治理规则"`license_status=UNKNOWN` 只记录、不下载"与本评估天然一致：授权未落地时，任何条目都应停在待审。
- 仓内已有的 `BBCLearningEnglishProvider` 与 `VOALearningEnglishProvider` **不得注册**：BBC 属本节 §2.4 的"须书面确认"，VOA 是美音（`en-US`，按规则直接排除）。

## 8. 决策记录：governance 两条失败测试放弃修复

- 决定（2026-11-18，用户确认）：**放弃修复** `test_non_british_accent_is_recorded_and_never_ingested`（`en-au` 未规范为 `en-AU`）与 `test_quarantined_material_is_isolated_from_the_new_weekly_pool`（合规素材就位后周测池仍为空）。
- 直接后果：**一旦有素材入库，周测新题池会抛 `ValueError: No verified material matches the active training source`，周测闭环不可用。** 当前库为空，所以这个缺陷暂时不显形。
- 恢复条件（三者任一触发就必须重启修复）：
  1. 任何一次真实素材入库；
  2. 准备启用周测/8 周升级流程；
  3. 验收要求恢复"全部测试绿"。
- 当前测试基线保持：169 passed / 2 failed（`42c6645`）。

## 9. 未解决项（需要用户决策）

1. **是否发书面授权询问？** 发给谁（50languages / BBC / 两家都发）？我可以起草并给出中英双语版本，但发信由你决定。
2. **是否接受第三档形态（人工获取 + 本地导入）**？如果接受，先把"人工导入"路径做成可用的最小流程，不碰自动抓取。
3. **是否投入下一轮评估开放许可候选**（audio-lingua / LibriVox / OpenLearn）？这会把"单一主平台 + 课程顺序"的假设改成"素材池 + 自建难度分级"，属于产品形态变更，需要单独 Plan Gate。

## 10. 证据附录（抓取结果）

| URL | 抓取结果 | 用途 |
|---|---|---|
| [goethe-verlag.com/agb.php](https://www.goethe-verlag.com/agb.php) | 200，全文 | 50languages §4/§5/§6 原文 |
| [50languages.com/licence](https://50languages.com/licence) | 200（正文截断） | CC BY-NC-ND 3.0 US 声明 |
| [50languages.com/robots.txt](https://50languages.com/robots.txt) | 200 | 路径规则与 Content-Signal |
| [50languages.com/terms-conditions](https://50languages.com/terms-conditions) | 200（正文 JS 渲染，截断） | 仅确认存在，未引用正文 |
| [britishcouncil.org/terms](https://www.britishcouncil.org/terms) | **403** | 改用 Wayback 快照 |
| [Wayback: britishcouncil terms（2026-01-01）](https://web.archive.org/web/20260101100312/https://www.britishcouncil.org/terms) | 200 | §10/§14 原文（二手复核） |
| [learnenglish.britishcouncil.org/terms-use](https://learnenglish.britishcouncil.org/terms-use) | **403** | 记录访问控制存在 |
| [breakingnewsenglish.com/copyright.html](https://breakingnewsenglish.com/copyright.html) | 200，全文 | BNE 全部禁止条款 |
| [breakingnewsenglish.com/robots.txt](https://breakingnewsenglish.com/robots.txt) | 200（空 Disallow） | 抓取许可 vs 使用许可的区分 |
| [bbc.co.uk/worldservice/learningenglish/terms.shtml](https://www.bbc.co.uk/worldservice/learningenglish/terms.shtml) | 200（已归档） | LE 使用条款 |
| [bbc.co.uk/worldservice/learningenglish/copyright.shtml](https://www.bbc.co.uk/worldservice/learningenglish/copyright.shtml) | 200（已归档） | LE 版权声明 |
| [bbc.co.uk/robots.txt](https://www.bbc.co.uk/robots.txt) | 200 | 现行"no scraping / no datasets"声明 |
| [bbc.co.uk/learningenglish/english/features/6-minute-english](https://www.bbc.co.uk/learningenglish/english/features/6-minute-english) | 200 | 节目结构与下载入口 |
| [6ME transcript PDF 样例](https://downloads.bbc.co.uk/learningenglish/features/6min/250703_6_minute_english_how_do_you_say_sorry_transcript.pdf) | 未下载复核 | 文字稿可得性（来自检索索引） |
| [audio-lingua.ac-versailles.fr](https://audio-lingua.ac-versailles.fr) | **连接失败** | 开放许可候选，可达性待验证 |
| audio-lingua.eu | 200（**已变为赌博推广页**） | 同名域名被占用，不可用 |

**方法学限制**：本评估以"条款文字"为准，不构成法律意见；BC 与 50languages 的部分条目为快照/索引复核；未对任何 mp3 做批量获取验证。
---

## 附录 A：50languages 补充核查（第二轮，2026-11-18）

第一轮抓取被超长导航菜单截断，第二轮补充核查得到三点关键事实，**不改变结论，但改变可行路径**。

### A.1 文字稿零授权已被直接原文确认

许可页正文（本机抓取时被截断，该句由第二轮抓取确认）：

> "The Creative Commons notice applies to the audio files published by 50LANGUAGES."
> "All written content is copyrighted and remains protected. All rights reserved."

这坐实了 §2.1 的判断：**CC 只覆盖音频，文字稿没有任何许可**。LLA 需要官方文字稿做逐字判分，这是唯一"必输"的一环。

### A.2 官方有整包下载，不需要逐页抓取

- book2 官方提供每对语言一个整包 zip，另有**单语包**（只含目标语言音轨）：
  - 双语整包形如 `…/book2/EN/ENZH/ENZH-all.zip`
  - 单语包形如 `…/_1language/ENEN.ZIP`
- 体量：约 90MB / 100 课，另有 1–10 课分卷（约 10MB）
- 课程数 100，官方定位 **CEFR A1–A2**，每课约 18 句
- **English (UK) 与 English (US) 是两套独立音频与独立目录**（`en` vs `em`），这对"发音一致性"是四家里最好的结构性保证

**但是**：托管 zip 的镜像域 `www.50idiomas.com` 的 robots.txt 为 `User-agent: * / Disallow: /`（全站禁止爬虫）。用户手工点击下载不等于爬取，但**脚本化批量下载仍是 §6 "download tools / bulk by automated means" 的射程**。

### A.3 对可行路径的修正

- **获取**：从"必须逐页抓"变为"存在官方整包"——若走人工获取，`ENEN.ZIP` 是最干净的入口（一个包 = 100 课，英音专用）。
- **改编**：整包仍是"整课音频"，每课约 18 句、男女声合并在同一文件内（官方原话："Most languages have male and female voices combined in one MP3 file."）。LLA 的逐句听写**必须**切句，而 ND 不授权改编——这一堵墙没有因为整包而消失。
- **结论**：50languages 从"三项都不成立"收紧为"**两项必须先拿书面许可（文字 + ND），一项可走人工下载**"。它是四家里**内容结构最匹配需求**的一家（100 课 / A1–A2 / 顺序固定 / 英音独立音轨），也是**授权最需要谈判**的一家。

### A.4 若要向 50LANGUAGES 发询问，需一并问清的三件事

1. 官方文字稿（英文原句）能否获得本地复制与展示许可；
2. 把 mp3 切成逐句片段并生成时间轴，是否被其视为 Derivative；若是，能否出具"仅本地、单用户、不再分发"的书面豁免；
3. `_1language/ENEN.ZIP` 内是"100 个整课文件"还是"逐句文件"——若是逐句，则问题 2 可直接消失。
---

## 附录 B：50languages English (UK) 官方素材入口（2026-11-18 已实测）

以下链接均以 HTTP `HEAD` / 单页 `GET` / zip 尾部 `Range` 请求核验，返回码与体积为实测值；**未下载任何整包音频**。

| 用途 | 链接 | 实测 |
|---|---|---|
| **English (UK) 单语音频整包（100 课）** | https://www.50idiomas.com/_1language/ENEN.ZIP | `200`，**56.6 MB**，`application/zip` |
| 对照：English (US) 单语音频包（**禁止使用**） | https://www.50idiomas.com/_1language/EMEM.ZIP | `200`，62.3 MB |
| 对照：英文界面 → 其他语言双语包示例 | https://www.50idiomas.com/book2/EN/ENAD/ENAD-all.zip | `200`，101.1 MB（可证明"~100 MB / 100 课"的量级） |
| 课程文字页（中文界面 → English UK，每课一页） | https://www.goethe-verlag.com/book2/ZH/ZHEN/ZHEN001.HTM … `/ZHEN100.HTM` | `200`（`ZHEN002.HTM` 实测 141 KB，标题"英语 UK: 中文 » 英语 UK"） |
| 课程总入口（选母语/目标语言、下载按钮） | https://www.goethe-verlag.com/book2/EN/ （English UK 界面）、https://www.goethe-verlag.com/book2/ZH/ （中文界面） | `200` |

**包内结构（实测）**：`ENEN.ZIP` 的中央目录里是 **100 个整课文件** `ENEN001.mp3 … ENEN100.mp3`，**没有**逐句文件。也就是说：

- 好消息：一个包 = 100 课，A1–A2，英音专用，不需要逐页抓取；
- 坏消息：**切句仍然必须做**（每课约 18 句、男女声混在同一文件），所以 ND 的"禁止修改"问题不会因为整包而消失；
- 文字稿不在 zip 里，只能从 `ZHENnnn.HTM` 页面取，而文字"All rights reserved"。

**授权边界（对个人本地使用）**：音频可非商业复制/分享并署名 Goethe Verlag GmbH（CC BY-NC-ND 3.0 US）；**不得分发修改版**、不得商用；文字无许可。因此正确用法是：**你本人下载 → 本地导入 → 仅自己训练 → 不对外分发**。