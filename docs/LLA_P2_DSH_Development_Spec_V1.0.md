# LLA P2 DSH Development Specification V1.0

- **Version:** V1.0
- **Date:** 2026-08-27
- **Audience:** DSH development
- **Scope:** P2-1 Long-term Training Dashboard, P2-2 Listening Memory Deepening, P2-3 Difficulty Progression History
- **Dependencies:** P0 Training Core, P1 material candidate pipeline and difficulty progression, SQLite/local files, existing P0/P1 API contracts
- **Non-regression rule:** This specification does not authorize changes to P0 Training Core, P0 state transitions, P0 scoring rules, P0 Weekly Gate behavior, or P1 upgrade behavior.
- **Current implementation note:** The repository currently contains both the formal P1 eight-week difficulty service and a legacy `/api/materials/next`/`MaterialRecommender` path. They must remain isolated in P2.

## 1. Product objective

P2 adds a long-term observation and explanation layer so the user can answer:

1. How much valid learning time has accumulated?
2. Is first blind-listen comprehension improving?
3. On average, on which listen does a target become recognizable from speech?
4. Which listening targets remain difficult over time?
5. Are Weekly Test scores and Gate outcomes becoming stable?
6. Are reading speed, pause, and stress improving independently?
7. How has the user progressed from VOA Slow to near-normal and normal speed?

P2 is a read-model, history, and recommendation layer. Training Core remains the sole authority for training state and pass/fail decisions.

## 2. Goals and non-goals

### 2.1 Goals

- Provide switchable time-range views.
- Preserve raw evidence, sample sizes, source type, missing-data reasons, and metric versions.
- Provide a single-value first-comprehension curve using the frozen mapping in this specification.
- Deepen Listening Memory without turning it into a vocabulary notebook.
- Keep ordinary training Memory strictly separate from Weekly Test data.
- Make P1 upgrade, refusal, cooldown, and downgrade history visible and explainable.
- Support historical backfill without destroying raw data or fabricating unreliable fields.
- Default review suggestions to enabled while giving the user explicit controls.

### 2.2 Non-goals

P2 must not:

- Change the P0 material state machine or listening/reading completion rules.
- Change the P0 exact-match standard, 80% Weekly Test threshold, or reading dimension gates.
- Make cumulative hours trigger an upgrade or downgrade.
- Automatically upgrade or downgrade the difficulty Stage.
- Use listening difficulty to change reading requirements or scores.
- Put Weekly Test attempts into ordinary Listening Memory.
- Introduce a new WPM-based upgrade algorithm.
- Treat LLM output as authoritative evidence.
- Expose transcript content during blind listening or dictation.
- Add multi-user, cloud sync, social, gamification, or course-management features.

## 3. Frozen P0/P1 boundaries

### 3.1 P0 authority

P0 remains authoritative for:

- First blind listen and first comprehension check.
- Dictation attempts, error types, Hint, Reveal, and exact completion.
- Basic Listening Memory evidence.
- Reading speed/pause/stress results.
- Weekly Test, reinforcement, and targeted retest behavior.
- Valid learning-time logging.

### 3.2 P1 authority

P1 remains authoritative for:

- VOA Slow as the initial provider.
- Clear/Acceptable/Poor quality treatment and complete Transcript requirements.
- Candidate selection followed by user-confirmed preparation.
- Formal Stage 1 → Stage 2 → Stage 3 progression.
- Eight consecutive stable Weekly Gate passes for eligibility.
- User decision before upgrade.
- Four-week cooldown after KEEP_CURRENT or DECIDE_LATER.
- One-Stage-at-most upgrade and Stage 3 cap.

### 3.3 P2 authority boundary

P2 may create additive P2 configuration, derived views, audit rows, and history events. P2 may not silently rewrite P0/P1 source records. If a source correction is needed, retain the original value and record the correction/backfill version.

## 4. Module scope

### P2-1: Long-term Training Dashboard

Read-only dashboard and drill-down views for learning time, first comprehension, listening recognition, Weekly Test performance, reading dimensions, and current difficulty status.

### P2-2: Listening Memory Deepening

Target-level and episode-level history, user-configurable short/long difficulty criteria, historical backfill, and review suggestions.

### P2-3: Difficulty Progression History

An append-only, user-observable timeline for Weekly Gate records, upgrade eligibility, prompts, decisions, cooldowns, material Stage facts, downgrade suggestions, and confirmed downgrades.

## 5. Authoritative data sources and logical data model

### 5.1 Source-of-truth table

| Domain fact | Authoritative source | P2 use |
|---|---|---|
| Valid learning time | Closed `training_time_logs` | Hours and time-range aggregation |
| Cached time totals | `learning_stats` | Reconciliation only; never add to raw logs |
| First comprehension | `comprehension_checks` where `phase=FIRST` | First-listen curve |
| Ordinary dictation evidence | `dictation_attempts` joined to `sentences` | Listening Memory |
| Ordinary Memory aggregate | `listening_memory` | Legacy aggregate/reference only until semantic reconciliation |
| Reading practice | `reading_attempts` | Practice trend |
| Weekly dictation score | `weekly_assessments`, `weekly_test_items` | Weekly performance trend |
| Weekly reading result | `weekly_assessments.reading_dimension_results` | Weekly reading/Gate trend |
| Formal Stage progression | `weekly_gate_records`, `upgrade_prompts`, `training_difficulty_profiles`, prepared `materials` | Difficulty history |
| Candidate quality | `material_candidates`, `audio_quality_reports`, `search_audits` | Source metadata only; not learning performance |

### 5.2 Required logical P2 objects

The implementation may choose table or view names, but it must provide these logical objects:

- `DashboardQuery`: scope, date range, aggregation granularity, and source filters.
- `MetricPoint`: metric ID, period, value, unit, sample count, source kind, missing reason, and metric version.
- `MemoryTargetAggregate`: normalized target, source occurrences, qualifying episodes, dates, first-correct distribution, Hint/Reveal counts, difficulty classification, and confidence.
- `MemoryThresholdConfig`: user-selected short window, long window, minimum episode count, minimum date count, and explicit configuration version.
- `ReviewPreference`: enabled/disabled, snoozed-until, per-target disable, batch pause, global pause, and frequency policy.
- `DifficultyEvent`: immutable event type, timestamp, Stage before/after, source record IDs, actor, reason, and policy version.
- `BackfillAudit`: source record, fields changed/derived, backfill time, source schema version, metric version, and reliability.

## 6. Metric dictionary and formulas

Metric IDs below are contract identifiers. They must be stable across API and UI.

### `P2.TIME.TOTAL_HOURS`

```text
total_hours = SUM(active_seconds of closed legal training_time_logs) / 3600
```

Included activities remain the P0 set: first full listen, dictation, second full listen, reading, full reading assessment, Weekly Test, reinforcement, and retest.

Do not include open logs, long pauses, idle time, system wait, LLM wait, or report browsing.

### `P2.TIME.WINDOW_HOURS`

Apply the user-selected date range to the same closed-log set. The existing `calendar`/`rolling7` weekly policy remains available where a weekly aggregate is shown; the dashboard date range must be explicit in the response.

### `P2.COMPREHENSION.FIRST_CURVE`

Use exactly one `FIRST` comprehension record per prepared material. Preserve:

- `raw_band`
- `mapped_score`
- `mapping_version`
- `sample_count`

Frozen mapping:

| Raw band | Mapped score |
|---|---:|
| `<30%` | 15 |
| `30–50%` | 40 |
| `50–70%` | 60 |
| `>70%` | 85 |

The mapping is ordinal and descriptive, not a claim of measured comprehension precision. The curve must retain the raw band and show the number of materials in each aggregate period. No `SECOND` check may replace a missing `FIRST` check.

### `P2.MEMORY.FIRST_CORRECT_LISTEN`

For each target occurrence and recognition episode:

```text
first_correct_listen_count
  = listen_count on the first exact attempt
  where revealed = false and hint_level = 0
```

An exact answer after Hint is excluded from this metric. An answer known only after Reveal is excluded from this metric, but its target may enter difficulty statistics.

`attempt_number` is the submission sequence and must never be substituted for `listen_count`.

### `P2.MEMORY.DIFFICULTY`

Use only ordinary-training evidence. Qualifying evidence includes non-`SPELLING` listening errors, active blanks, Hint usage, and Reveal usage. A single miss is evidence, not automatically a long-term classification.

The classification must use the user-selected `MemoryThresholdConfig`:

- short-term window;
- long-term window;
- minimum qualifying episode count;
- minimum distinct-date count.

The interface must show these values beside every classification. If the user has not selected a configuration, return `UNCONFIGURED` rather than applying hidden defaults.

### `P2.WEEKLY.DICTATION_SCORE`

```text
dictation_score = exact TEST items / all TEST items × 100
```

Show the score with item count, Weekly Gate status, reinforcement status, and retest status. Do not replace the original test score with the later Gate result.

### `P2.READING.PRACTICE_DIMENSION`

For `reading_attempts`, show separate status distributions for `speed_result`, `pause_result`, and `stress_result`.

Where numeric fields exist:

```text
speed_ratio = user_duration / reference_duration
pause_delta = user_pause_count - reference_pause_count
```

Do not create a numeric stress score if the source only contains a status. Do not average the three dimensions into one score.

### `P2.READING.WEEKLY_DIMENSION`

Use `weekly_assessments.reading_dimension_results` as the Weekly Test series. It is separate from practice attempts and must retain independent speed/pause/stress results.

### `P2.DIFFICULTY.STREAK`

Use P1 `weekly_gate_records` for the formal eight-week streak. Do not infer a formal streak from hours, material count, Memory count, or the legacy recommender.

## 7. Time-range switching

The dashboard must allow the user to select a date range. Each response must return:

- `range_start`;
- `range_end`;
- timezone or timestamp interpretation;
- aggregation granularity;
- source inclusion rules;
- empty-period behavior.

Supported view shapes should include full history, recent periods, and an arbitrary date range. Exact preset labels may be implementation-defined, but the active range must always be visible.

The dashboard must not silently switch between calendar weeks and rolling seven-day windows. If a weekly chart uses the existing weekly policy, expose that policy in the explanation panel.

## 8. Dashboard information architecture

### 8.1 Summary

- Total valid learning hours.
- Selected-range hours.
- Current formal Stage label.
- Current Weekly Gate state.
- Current review-suggestion status.

### 8.2 Long-term trend

- Learning hours by selected period.
- Single-value first-comprehension curve.
- Weekly dictation score and Gate state.
- Separate reading speed, pause, and stress lanes.

### 8.3 Listening Memory

- Long-term difficulty list.
- Short-term difficulty list.
- Target details and evidence.
- Active review suggestions.
- User-selected threshold configuration.

### 8.4 Difficulty history

- Stage bands: `VOA Slow` → `接近正常语速` → `正常语速`.
- Eight-week stability counter.
- Eligibility and Prompt markers.
- Upgrade confirmation, refusal, and cooldown markers.
- Downgrade suggestion, user request, confirmation, and decline markers.
- Prepared-material metadata: actual speech rate, duration, source, and quality.

### 8.5 Explanation panel

Every panel must expose time range, sample count, source kind, missing reason, backfill status, and metric version.

## 9. Listening Memory requirements

### 9.1 Four-layer objects

1. **Target:** normalized word or phrase.
2. **TargetOccurrence:** target in a particular material, sentence, and Part.
3. **RecognitionEpisode:** one sentence-level listening episode, aggregating its retries rather than counting every submission as a new episode.
4. **TargetAggregate:** cross-material and cross-date summary.

### 9.2 Evidence rules

- Ordinary training only.
- Weekly Test is excluded from ordinary Memory.
- Reinforcement and Targeted Retest must be labeled separately if displayed.
- `SPELLING` never qualifies as a listening difficulty.
- Reveal-only recognition is excluded from average first-correct listen, but may qualify as difficulty evidence.
- Hint-after-correct is excluded from average first-correct listen.
- All target classifications must be drillable to source attempts.

### 9.3 User threshold configuration

The UI must provide editable fields for short window, long window, minimum qualifying episodes, and minimum distinct dates. The current confirmed visible defaults are:

- short-term: 14 days;
- long-term: 8 weeks;
- minimum episodes: 3;
- minimum distinct dates: 2.

The user may modify them. The active values must be stored with a version and returned with the result. The short-term window cannot exceed the long-term window. Validation errors must be explicit.

### 9.4 Review suggestions

Suggestions are enabled by default. A suggestion must state the triggering evidence and never modify P0/P1 state.

Required controls:

- disable one target;
- snooze one target;
- batch pause;
- global disable;
- restore suggestions;
- view and delete suggestion history without deleting raw training history.

Default frequency policy: the same target is suggested at most once every seven days; after reaching the limit it is temporarily deferred. The interval and limit are configuration values and must be visible and adjustable.

Privacy behavior:

- Default storage is local.
- Enabling suggestions must not implicitly send audio, transcript, or dictation text externally.
- External AI use requires a separate user choice.
- Suggestion preferences and raw training data have separate deletion controls.

## 10. Historical backfill

Backfill is allowed, but it must be additive and auditable.

### 10.1 Mandatory audit fields

- original source record ID;
- backfill timestamp;
- source schema/data version;
- target metric/semantic version;
- fields derived;
- reliability: `RELIABLE`, `CONDITIONAL`, or `UNRELIABLE`;
- reason for any field left unavailable.

### 10.2 Usually reliable fields

- raw dictation text;
- exact/non-exact status;
- listen count;
- Hint/Reveal flags;
- error type;
- material/sentence/date association;
- exclusion of `SPELLING`;
- Weekly Test separation;
- Stage metadata already stored on prepared materials.

### 10.3 Conditional fields

- first-correct listen count;
- episode boundaries;
- target phrase association;
- short-/long-term classification;
- historical averages.

These fields require unambiguous source linkage; otherwise mark them Conditional or Unreliable.

### 10.4 Fields that must not be fabricated

- User intent not recorded in source data.
- Recognition of a target when no target evidence exists.
- Historical review preferences.
- Unrecorded audio-analysis results.

## 11. Difficulty history and event model

### 11.1 Formal source of truth

Only P1 formal eight-week records, user decisions, and actual Stage changes are official difficulty history. The legacy `/api/materials/next` two-pass recommender is isolated and must never create or alter P2 formal Stage events.

### 11.2 Event types

| Event | Rule |
|---|---|
| `WEEKLY_GATE_RECORDED` | Copy the P1 Gate result and evidence references |
| `STREAK_UPDATED` | Record increase, reset, or missing-week break |
| `UPGRADE_ELIGIBLE` | Eight consecutive formal stable passes |
| `UPGRADE_PROMPTED` | Prompt shown to user |
| `UPGRADE_DECIDED` | Confirm, keep current, or decide later |
| `COOLDOWN_STARTED` | Keep current or decide later starts four-week cooldown |
| `COOLDOWN_EXPIRED` | Cooldown end becomes observable |
| `STAGE_CHANGED` | Confirmed Stage increase or decrease |
| `MATERIAL_PREPARED` | Prepared material and actual Stage/source metadata |
| `MATERIAL_SKIPPED` | User skipped a material |
| `DOWNGRADE_SUGGESTED` | Two consecutive Weekly Gates not passed |
| `USER_DOWNGRADE_REQUESTED` | User requests a downgrade |
| `DOWNGRADE_CONFIRMED` | Explicit confirmation applies the downgrade |
| `DOWNGRADE_DECLINED` | User declines a system suggestion |

### 11.3 Upgrade rules

- Eight consecutive formal Weekly Gate passes are required for eligibility.
- Eligibility does not change Stage.
- A user decision is required.
- One upgrade may cross at most one Stage.
- KEEP_CURRENT and DECIDE_LATER retain the Stage and start the P1 four-week cooldown.
- Stage 3 is the maximum.
- Cumulative hours do not trigger upgrade.

### 11.4 Downgrade rules

- A system suggestion is triggered after two consecutive formal Weekly Gates that are not passed.
- The system only suggests; it never applies a downgrade automatically.
- A user-initiated downgrade requires explicit confirmation.
- A single downgrade crosses at most one Stage.
- Stage 1 is the minimum.
- After a confirmed downgrade, the formal consecutive-pass count is zero.
- The new Stage must accumulate a fresh eight-week sequence for a future upgrade.
- No downgrade event may delete prior history.

## 12. Data quality, privacy, and explainability

- Failed analysis must not become PASS.
- Missing data must not become zero or FAIL.
- Reading results must expose analysis/threshold version where available.
- Every chart must expose sample count and source type.
- Legacy/backfilled values must be visibly labeled.
- Raw local data remains the source of truth.
- Audio, transcripts, and dictation text must remain local unless the user explicitly enables an external provider.
- Deleting a derived view must not delete raw training records unless the user explicitly chooses both.
- P2 explanations must be deterministic and traceable; LLM narratives cannot override facts.

## 13. Idempotency, audit, migration, and rollback

### 13.1 Idempotency

- Repeating a dashboard read creates no new facts.
- Repeating a backfill with the same source/version returns the same result.
- Replaying an upgrade or downgrade decision does not create a second Stage change.
- Replaying a history event must resolve by stable event key.

### 13.2 Audit

Store source IDs, event IDs, timestamps, actor (`SYSTEM` or `USER`), policy version, metric version, and reason codes.

### 13.3 Migration

Future implementation may add P2-specific tables/views after approval. It must not destructively alter P0/P1 source data or require a P0 state-machine change. Additive migration is preferred.

### 13.4 Rollback

Rollback must disable P2 read models and derived events without deleting raw P0/P1 data. Backfill rows must remain inspectable. A failed P2 migration must leave P0/P1 training usable.

## 14. Interface contract

The exact framework route names may follow repository conventions, but the following logical contracts are required:

### `P2-DASHBOARD-READ`

Inputs: scope, date range, granularity, optional source filters.

Outputs: summary metrics, metric points, sample counts, missing reasons, active configuration, mapping versions, and current Stage/Gate status.

### `P2-MEMORY-READ`

Inputs: scope, date range, user threshold configuration.

Outputs: four-layer Memory objects, target evidence, difficulty classification, confidence, and suggestion state.

### `P2-MEMORY-CONFIG`

Inputs: user-selected windows/counts and suggestion preferences.

Outputs: saved configuration, validation result, configuration version, and effective timestamp.

### `P2-DIFFICULTY-HISTORY-READ`

Outputs: ordered immutable events, Stage bands, Gate streaks, Prompt/decision/cooldown markers, downgrade events, and material metadata.

### `P2-BACKFILL-AUDIT-READ`

Outputs: source record, derived fields, reliability, backfill version/time, and unavailable-field reasons.

No P2 read or configuration contract may return full transcript content in a blind-listening context.

## 15. Developer pre-self-check

- [ ] No P0 state transition or pass/fail rule changed.
- [ ] No P1 eight-week, confirmation, cooldown, Stage cap, or single-Stage rule changed.
- [ ] Dashboard date range is visible and returned by the API.
- [ ] First-comprehension curve uses 15/40/60/85 and retains raw band, mapping version, and sample count.
- [ ] Hint-after-correct and Reveal-only targets are excluded from average first-correct listen.
- [ ] `attempt_number` is not used as listen count.
- [ ] User Memory thresholds are visible, editable, versioned, and have no hidden defaults.
- [ ] Weekly Test is excluded from ordinary Memory.
- [ ] `SPELLING` is excluded from listening difficulty.
- [ ] Backfill preserves raw data and records reliability/version/time.
- [ ] Suggestions default to enabled and support disable, snooze, batch pause, global pause, and seven-day frequency limit.
- [ ] System downgrade suggestions never auto-apply.
- [ ] Confirmed downgrade crosses at most one Stage and resets the eight-week counter.
- [ ] Legacy `/api/materials/next` history is isolated.
- [ ] Empty, missing, failed-analysis, and Legacy states are explicit.
- [ ] Idempotency and audit fields are covered.
- [ ] P0/P1 full regression is green before handoff.
