# LLA P2 Codex Independent Acceptance Test V1.0

- **Version:** V1.0
- **Date:** 2026-08-27
- **Audience:** Codex independent acceptance
- **Scope:** P2-1 Long-term Training Dashboard, P2-2 Listening Memory Deepening, P2-3 Difficulty Progression History
- **Dependencies:** P0/P1 accepted baseline, P2 DSH Development Specification V1.0, clean SQLite database, test fixtures, real or deterministic audio where required
- **Independence rule:** Acceptance evaluates user-observable behavior, API responses, persisted facts, and regression results. It must not assume DSH internal implementation is correct.
- **Non-regression rule:** This test does not authorize changing P0 Training Core, P0 scoring, P0 state transitions, P1 upgrade rules, or P1 material behavior.

## 1. Acceptance principles

P2 may be accepted only when:

- The displayed result is reproducible from persisted source facts.
- Metrics expose range, sample size, source type, missing reason, and version where applicable.
- No ordinary training fact is double-counted.
- Weekly Test is isolated from ordinary Listening Memory.
- Historical backfill is auditable and does not destroy raw data.
- Difficulty history uses formal P1 facts and does not import the legacy two-pass recommender.
- P0/P1 regression remains green.

The following are not sufficient by themselves:

- A page that renders.
- A successful build.
- A mock-only test.
- A single screenshot.
- A passing internal unit test without persisted-data verification.

## 2. Required test environment and fixtures

### 2.1 Environment

Record:

- OS and runtime versions.
- Application commit/ref under test.
- Database path and clean-state method.
- P2 metric version and mapping version.
- Timezone and weekly-window policy.
- Speech/reading analyzer version if reading data is generated.

### 2.2 Test fixtures

Prepare a clean database containing:

- At least three prepared materials across multiple dates.
- At least two Parts and multiple sentences per material.
- First comprehension checks in all four bands.
- Ordinary dictation episodes with first-listen success, multi-listen success, Hint success, Reveal-only success, and spelling-only errors.
- The same target repeated across materials and dates.
- At least one Weekly Test with ordinary-training targets and at least one reinforcement/retest path.
- Reading practice attempts with separate speed, pause, and stress results.
- Weekly Gate records covering pass, fail/reinforcement, eight stable passes, cooldown, and Stage change.
- Historical records that can be reliably backfilled and records with deliberately missing/ambiguous fields.
- Empty and partially populated database variants.

Every fixture must have a source-data manifest containing IDs, timestamps, expected categories, and expected inclusion/exclusion.

## 3. Evidence requirements

For every test, retain:

- Request/response or UI operation evidence.
- Relevant database rows before and after the operation.
- Metric range, sample count, source kind, and version.
- Screenshot or screen recording for UI-only behavior.
- Exact error response for rejected operations.
- Git diff and regression command output.

Acceptance evidence must be sufficient for an independent reviewer to recompute the expected result without reading DSH implementation code.

## 4. P2-1 dashboard acceptance

### P2-1-01 Summary and range switching

**Steps**

1. Open the dashboard with a populated database.
2. Record the default selected range.
3. Switch to another supported range.
4. Select an arbitrary date range containing only a subset of source records.
5. Refresh the page and repeat the query.

**Expected**

- The active range is visible.
- Summary hours change according to the selected range.
- Results are reproducible after refresh.
- The response exposes range start, range end, granularity, and timestamp interpretation.
- Dashboard reads create no duplicate source or history records.

**Blocking:** Any range silently using another range, or any refresh that changes persisted counts.

### P2-1-02 Total and window learning hours

**Steps**

1. Insert/prepare closed valid activity logs with known seconds.
2. Include an open log, a long-pause/idle-excluded log, and a system-wait record if the environment supports them.
3. Query total and selected-range hours.
4. Compare with raw `training_time_logs` and `learning_stats`.

**Expected**

```text
displayed_hours = SUM(closed legal active_seconds) / 3600
```

- Open, invalid, idle, and system-wait time is excluded.
- `learning_stats` is not added to raw logs.
- The result is within the documented display rounding while raw seconds remain exact.
- Session, weekly, and total values do not double-count.

### P2-1-03 First comprehension curve and mapping boundaries

**Steps**

1. Create one `FIRST` check in each band: `<30%`, `30–50%`, `50–70%`, `>70%`.
2. Create `SECOND` checks with different values for the same materials.
3. Query the dashboard by period.
4. Inspect raw band, mapped score, mapping version, and sample count.

**Expected mapping**

| Raw band | Expected score |
|---|---:|
| `<30%` | 15 |
| `30–50%` | 40 |
| `50–70%` | 60 |
| `>70%` | 85 |

- The curve uses only `FIRST` checks.
- `SECOND` checks do not replace missing `FIRST` checks.
- Original band, mapped score, mapping version, and sample count are all retained.
- Boundary labels are not silently normalized into a different band.

**Blocking:** Any score other than 15/40/60/85, missing raw band/version/sample count, or use of SECOND as FIRST.

### P2-1-04 Weekly dictation score and Gate trend

**Steps**

1. Create Weekly Tests with known TEST item counts and exact outcomes.
2. Create a failed test followed by reinforcement and retest.
3. Query the trend before and after retest.

**Expected**

- Initial Weekly Test score equals exact TEST items divided by all TEST items times 100.
- Initial score remains visible after reinforcement/retest.
- Final Gate/reinforcement/retest status is shown separately.
- A week without a test is missing, not zero.

### P2-1-05 Reading speed/pause/stress trends

**Steps**

1. Create reading practice attempts with mixed PASS/CLOSE/FAIL values.
2. Create Weekly Reading results with different dimension outcomes.
3. Query the dashboard by practice and Weekly Test source if filters exist.

**Expected**

- Speed, pause, and stress are shown as three independent series.
- Practice attempts and Weekly Test results are not merged.
- No average score hides an individual dimension FAIL.
- If numeric duration/pause data exists, the documented ratio/delta is reproducible.
- No numeric stress value is fabricated when the source contains only a status.
- A week without Reading is “not applicable”, not FAIL.

### P2-1-06 Empty, missing, and failed-analysis states

**Steps**

1. Query an empty database.
2. Query a period with no records.
3. Include an incomplete comprehension, reading, time, or backfill record.
4. Include a failed reading analysis that did not produce a PASS record.

**Expected**

- Empty hours may display as zero only where zero is semantically correct.
- Missing performance data is labeled “no sample”, “not applicable”, or equivalent.
- Missing is never converted to zero or FAIL.
- Failed analysis is not displayed as PASS.
- The reason is visible through API or drill-down evidence.

## 5. P2-2 Listening Memory acceptance

### P2-2-01 First-correct listen semantics

**Steps**

1. Create a target answered exactly on listen 1 without Hint or Reveal.
2. Create a target answered exactly after multiple listens without Hint or Reveal.
3. Create a target answered exactly after Hint.
4. Create a target known only after Reveal.
5. Give the same target multiple submissions in one sentence episode.
6. Inspect `listen_count`, `attempt_number`, Hint, Reveal, and derived Memory output.

**Expected**

- The metric uses the first successful qualifying `listen_count`.
- Hint-after-correct is excluded from average first-correct listen.
- Reveal-only recognition is excluded from average first-correct listen.
- Reveal-only targets may still appear as difficulty evidence.
- `attempt_number` is not used as listen count.
- Multiple submissions in one episode do not become multiple independent episodes.

### P2-2-02 SPELLING exclusion

**Steps**

1. Submit a spelling-only error for a target.
2. Submit a non-spelling listening error for another target.
3. Query ordinary Memory and the difficulty list.

**Expected**

- The spelling-only target is absent from listening-difficulty counts.
- The non-spelling target appears with source evidence.
- Error details remain inspectable.

**Blocking:** Any `SPELLING`-only target classified as listening difficulty.

### P2-2-03 Cross-material and cross-date aggregation

**Steps**

1. Repeat one target across at least two materials and two dates.
2. Use different listen counts and outcomes for each occurrence.
3. Query Target, TargetOccurrence, RecognitionEpisode, and TargetAggregate views or their API equivalents.

**Expected**

- The target aggregates across materials without losing occurrence context.
- Distinct dates are correct.
- Episode count is not equal to raw submission count when retries belong to one episode.
- Every aggregate can be traced to source attempt IDs.

### P2-2-04 User-selectable thresholds

**Steps**

1. Verify visible initial configuration: short 14 days, long 8 weeks, minimum 3 episodes, minimum 2 dates.
2. Change each value independently.
3. Set short window greater than long window.
4. Clear the configuration if the UI supports an unconfigured state.
5. Query the same data under each configuration.

**Expected**

- Initial values are visible, editable, and versioned.
- Classification changes only according to the active values.
- Short window greater than long window is rejected with an explicit error.
- There are no hidden threshold values.
- Unconfigured values produce an explicit unconfigured state rather than silent defaults.
- The active thresholds are returned with each classification.

### P2-2-05 Historical backfill and reliability

**Steps**

1. Snapshot raw legacy records.
2. Run backfill once.
3. Inspect raw records and BackfillAudit records.
4. Run the same backfill again.
5. Include records with ambiguous target linkage or missing episode evidence.

**Expected**

- Raw records are unchanged.
- Each derived field has backfill time, marker, source/version, and reliability.
- Reliable fields are backfilled deterministically.
- Conditional fields are marked Conditional when linkage is not certain.
- Unreliable fields are not fabricated.
- Repeating backfill is idempotent.

### P2-2-06 Default-on review suggestions

**Steps**

1. Start with a new profile and verify suggestion state.
2. Create a target meeting the active difficulty conditions.
3. Observe the suggestion reason, target evidence, and timestamp.
4. Trigger the same target again within seven days.
5. Trigger it after seven days.

**Expected**

- Suggestions are enabled by default.
- The suggestion explains its source evidence and active thresholds.
- The same target is suggested at most once every seven days by default.
- A repeated suggestion inside the limit is temporarily deferred.
- After the interval, a new suggestion may be produced.

### P2-2-07 Review controls and privacy

**Steps**

1. Disable one target.
2. Snooze one target.
3. Pause suggestions in batch.
4. Disable suggestions globally, then restore them.
5. Delete suggestion history while retaining raw training records.
6. Verify network/privacy behavior according to the local-first deployment.

**Expected**

- Each control has an observable state and does not alter raw P0/P1 facts.
- Disabled and snoozed targets do not produce suggestions during the active restriction.
- Global disable works and can be reversed.
- Suggestion history deletion does not delete dictation attempts.
- Enabling suggestions does not silently transmit audio, transcript, or dictation text externally.

## 6. P2-3 difficulty history acceptance

### P2-3-01 Formal Stage source and label path

**Steps**

1. Create formal P1 Gate records and Stage changes.
2. Create materials through formal P1 preparation.
3. Query the difficulty history.

**Expected**

- The visible path is `VOA Slow → 接近正常语速 → 正常语速`.
- Formal history uses P1 eight-week records, user decisions, and actual Stage changes.
- Material entries include actual speech rate and source metadata.
- Hours, material count, or Memory difficulty do not create formal Stage changes.

### P2-3-02 Upgrade eligibility, confirmation, and one-Stage limit

**Steps**

1. Create seven passing formal weeks and verify no eligibility.
2. Create the eighth passing week and verify eligibility.
3. Verify Prompt creation.
4. Confirm upgrade.
5. Repeat at Stage 2 and attempt to cross more than one Stage.

**Expected**

- Eligibility appears only after eight consecutive formal stable passes.
- Prompt and eligibility are separate events.
- Upgrade requires user confirmation.
- One upgrade crosses at most one Stage.
- Stage 3 cannot be exceeded.
- The formal counter resets according to P1 behavior after an actual upgrade.

### P2-3-03 Refusal, delay, and four-week cooldown

**Steps**

1. Reach eligibility and create a Prompt.
2. Choose KEEP_CURRENT.
3. Verify Stage remains unchanged and cooldown is visible.
4. Attempt to prompt during cooldown.
5. Advance four weeks and verify the cooldown end.
6. Repeat using DECIDE_LATER.

**Expected**

- KEEP_CURRENT and DECIDE_LATER are distinguishable.
- Neither changes Stage.
- Both show a four-week cooldown.
- No new formal Prompt is produced during cooldown.
- After cooldown, eligibility can be evaluated again according to P1 rules.

### P2-3-04 System downgrade suggestion

**Steps**

1. Create one formal Weekly Gate failure.
2. Verify no downgrade is applied.
3. Create a second consecutive formal Weekly Gate failure.
4. Verify a downgrade suggestion appears.
5. Leave it unconfirmed, then decline it.

**Expected**

- The suggestion appears after two consecutive formal Gates not passed.
- The system does not automatically change Stage.
- No user response leaves Stage unchanged.
- Declining is recorded as a distinct event.
- The suggestion contains the two source Gate IDs and reason.

### P2-3-05 User-initiated downgrade and confirmation

**Steps**

1. From Stage 2 or Stage 3, request a downgrade.
2. Cancel or decline confirmation.
3. Repeat and confirm.
4. Attempt to cross two Stages.
5. Attempt to go below Stage 1.

**Expected**

- Active downgrade requires explicit confirmation.
- Cancel/decline causes no Stage change.
- Confirmed downgrade crosses at most one Stage.
- Stage 1 is the lower bound.
- A confirmed downgrade resets consecutive passing weeks to zero.
- The new Stage starts a fresh eight-week accumulation.
- Previous history remains visible.

### P2-3-06 Legacy recommender isolation

**Steps**

1. Create data produced by the legacy `/api/materials/next`/`MaterialRecommender` two-pass logic.
2. Create separate formal P1 eight-week records and Stage changes.
3. Query difficulty history and current Stage.

**Expected**

- Legacy two-pass outcomes do not create formal P2 Stage events.
- Formal P1 events remain the sole official history.
- Legacy-attributed material is either excluded from formal Stage history or visibly labeled as legacy/unattributed.
- No chart combines both rules into one streak or trajectory.

## 7. Idempotency and audit acceptance

### P2-7-01 Read idempotency

Repeat dashboard, Memory, history, and backfill audit reads. Confirm no new fact rows, duplicate suggestions, or duplicate events.

### P2-7-02 Decision idempotency

Replay the same upgrade and downgrade decision with the same and different request keys. Confirm one effective decision and one Stage change at most.

### P2-7-03 Event auditability

For each event, verify event ID, timestamp, actor, source IDs, Stage before/after, reason, and policy/metric version.

## 8. P0/P1 regression acceptance

Run the complete existing backend test suite and frontend production build. At minimum verify:

- P0 material state machine.
- Blind-listen transcript boundary.
- Dictation exact match and error classification.
- Hint/Reveal flow.
- Listening Memory spelling exclusion.
- Cross-day resume.
- Reading three-dimension scoring.
- Weekly Gate, reinforcement, and targeted retest.
- Learning-time aggregation.
- P1 candidate quality, candidate preparation, idempotency, and failure recovery.
- P1 eight-week eligibility, refusal, cooldown, one-Stage upgrade, and Stage 3 cap.

Any P2 change that causes a P0/P1 regression is a blocking failure.

## 9. Blocking items

The following are blocking:

- Wrong comprehension mapping, missing mapping version, or wrong sample count.
- Double-counted learning time, episodes, or Stage events.
- Hint or Reveal incorrectly included in average first-correct listen.
- `attempt_number` substituted for `listen_count`.
- `SPELLING` entering listening difficulty.
- Weekly Test entering ordinary Memory.
- Unreliable backfill fabricated as reliable.
- Suggestions not enabled by default or unable to be disabled/snoozed.
- Same target suggested more than once within seven days without explicit configuration.
- Automatic downgrade without confirmation.
- Downgrade crossing more than one Stage, going below Stage 1, or failing to reset the streak.
- Legacy recommender contaminating formal difficulty history.
- Missing/failed data displayed as a false zero, FAIL, or PASS.
- Raw data deletion when only derived data was requested.
- Any P0/P1 behavior or regression failure.

## 10. Known limitations classification

Use `P2 ACCEPTED WITH KNOWN LIMITATIONS` only when:

- The limitation is non-blocking.
- The affected metric is clearly labeled.
- Raw evidence remains available.
- A workaround or follow-up is documented.
- No false PASS, false FAIL, or false trend is presented.

Examples of potentially non-blocking limitations include an unavailable numeric stress value, a Legacy backfill field marked Unreliable, or a UI-only explanatory text issue that does not affect data correctness.

## 11. Final conclusion enumeration

The final report must use exactly one of:

### `P2 ACCEPTED`

Use only when all blocking tests pass, P0/P1 regression passes, and no known limitation affects correctness or user control.

### `P2 ACCEPTED WITH KNOWN LIMITATIONS`

Use when all blocking tests pass but documented non-blocking limitations remain.

### `P2 NOT ACCEPTED`

Use when any blocking item fails, evidence is insufficient, or P0/P1 behavior regresses.

## 12. Required independent acceptance report

The final report must include:

1. Environment and commit/ref.
2. Clean-state and fixture manifest.
3. Commands and API/UI operations executed.
4. Test matrix with PASS/FAIL/NOT VERIFIED.
5. Database evidence and recomputation notes.
6. P0/P1 regression results.
7. Known limitations with impact and workaround.
8. Exactly one final conclusion enum.
