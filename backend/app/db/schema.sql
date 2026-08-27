PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    material_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT,
    source_url TEXT,
    audio_path TEXT NOT NULL,
    transcript TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    speech_rate_wpm REAL,
    difficulty_level TEXT,
    status TEXT NOT NULL,
    part_1_start REAL,
    part_1_end REAL,
    part_2_start REAL,
    part_2_end REAL,
    part_3_start REAL,
    part_3_end REAL,
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS sentences (
    sentence_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES materials(material_id),
    part_no INTEGER NOT NULL CHECK (part_no BETWEEN 1 AND 3),
    sequence_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    UNIQUE(material_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS training_progress (
    material_id TEXT PRIMARY KEY REFERENCES materials(material_id),
    current_state TEXT NOT NULL,
    dictation_part_status TEXT NOT NULL DEFAULT '{"1": false, "2": false, "3": false}',
    current_sentence_id TEXT REFERENCES sentences(sentence_id),
    current_attempt INTEGER NOT NULL DEFAULT 0,
    reading_part_status TEXT NOT NULL DEFAULT '{"1": false, "2": false, "3": false}',
    full_reading_status TEXT NOT NULL DEFAULT 'LOCKED',
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dictation_attempts (
    attempt_id TEXT PRIMARY KEY,
    sentence_id TEXT NOT NULL REFERENCES sentences(sentence_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    user_text TEXT NOT NULL,
    listen_count INTEGER NOT NULL DEFAULT 1 CHECK (listen_count > 0),
    is_exact_match INTEGER NOT NULL CHECK (is_exact_match IN (0, 1)),
    hint_level INTEGER NOT NULL DEFAULT 0 CHECK (hint_level BETWEEN 0 AND 2),
    revealed INTEGER NOT NULL DEFAULT 0 CHECK (revealed IN (0, 1)),
    error_details TEXT NOT NULL DEFAULT '[]',
    memory_targets TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(sentence_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS comprehension_checks (
    check_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES materials(material_id),
    phase TEXT NOT NULL CHECK (phase IN ('FIRST', 'SECOND')),
    self_rating TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS listening_memory (
    target TEXT PRIMARY KEY,
    encounter_count INTEGER NOT NULL DEFAULT 0,
    first_listen_correct_count TEXT NOT NULL DEFAULT '{}',
    avg_attempt_before_correct REAL,
    hint_count INTEGER NOT NULL DEFAULT 0,
    reveal_count INTEGER NOT NULL DEFAULT 0,
    weekly_test_result TEXT,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reading_attempts (
    attempt_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES materials(material_id),
    scope TEXT NOT NULL CHECK (scope IN ('PART', 'FULL')),
    part_no INTEGER CHECK (part_no IS NULL OR part_no BETWEEN 1 AND 3),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    reference_duration REAL,
    user_duration REAL,
    speed_result TEXT,
    pause_result TEXT,
    stress_result TEXT,
    overall_pass INTEGER CHECK (overall_pass IS NULL OR overall_pass IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_assessments (
    week_id TEXT PRIMARY KEY,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    dictation_required INTEGER NOT NULL CHECK (dictation_required IN (0, 1)),
    reading_required INTEGER NOT NULL CHECK (reading_required IN (0, 1)),
    dictation_score REAL,
    dictation_pass INTEGER CHECK (dictation_pass IS NULL OR dictation_pass IN (0, 1)),
    reading_dimension_results TEXT NOT NULL DEFAULT '{}',
    gate_status TEXT NOT NULL,
    reinforcement_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_test_items (
    item_id TEXT PRIMARY KEY,
    week_id TEXT NOT NULL REFERENCES weekly_assessments(week_id),
    kind TEXT NOT NULL CHECK (kind IN ('TEST', 'REINFORCEMENT')),
    sentence_id TEXT,
    text TEXT NOT NULL,
    is_exact INTEGER NOT NULL DEFAULT 0 CHECK (is_exact IN (0, 1)),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_time_logs (
    time_log_id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    active_seconds INTEGER NOT NULL DEFAULT 0 CHECK (active_seconds >= 0),
    activity_type TEXT NOT NULL,
    material_id TEXT REFERENCES materials(material_id),
    session_id TEXT
);

CREATE TABLE IF NOT EXISTS learning_stats (
    stats_id INTEGER PRIMARY KEY CHECK (stats_id = 1),
    session_learning_seconds INTEGER NOT NULL DEFAULT 0,
    weekly_learning_seconds INTEGER NOT NULL DEFAULT 0,
    total_learning_seconds INTEGER NOT NULL DEFAULT 0,
    completed_materials INTEGER NOT NULL DEFAULT 0,
    listening_completed_materials INTEGER NOT NULL DEFAULT 0,
    fully_completed_materials INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO learning_stats(stats_id) VALUES (1);

-- ============ P1: material candidates, quality reports, difficulty ============

CREATE TABLE IF NOT EXISTS material_candidates (
    candidate_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    provider TEXT NOT NULL,
    provider_item_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    audio_url TEXT NOT NULL,
    transcript TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    speed_stage TEXT NOT NULL DEFAULT 'STAGE_1',
    audio_quality TEXT NOT NULL,
    audio_quality_report_id TEXT,
    transcript_status TEXT NOT NULL,
    candidate_status TEXT NOT NULL DEFAULT 'CANDIDATE',
    search_batch_id TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    idempotency_key TEXT,
    failure_code TEXT,
    audio_path TEXT,
    timestamped_sentences_json TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    UNIQUE(scope_id, content_fingerprint)
);

CREATE TABLE IF NOT EXISTS audio_quality_reports (
    report_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    audio_fingerprint TEXT,
    quality_level TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    threshold_config_version TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    failure_code TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_difficulty_profiles (
    scope_id TEXT PRIMARY KEY,
    current_stage TEXT NOT NULL DEFAULT 'STAGE_1',
    consecutive_pass_weeks INTEGER NOT NULL DEFAULT 0,
    upgrade_eligible INTEGER NOT NULL DEFAULT 0,
    last_upgrade_prompt_at TEXT,
    last_upgrade_decision TEXT,
    cooldown_until TEXT,
    last_upgrade_at TEXT,
    profile_version TEXT NOT NULL DEFAULT '1.0',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_gate_records (
    gate_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    training_week_id TEXT NOT NULL,
    stage_at_evaluation TEXT NOT NULL,
    gate_result TEXT NOT NULL,
    dictation_score REAL,
    read_aloud_score TEXT,
    read_aloud_attempted INTEGER NOT NULL DEFAULT 0,
    evaluation_reason_codes TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(scope_id, training_week_id, stage_at_evaluation)
);

CREATE TABLE IF NOT EXISTS upgrade_prompts (
    prompt_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    stage_at_prompt TEXT NOT NULL,
    prompt_status TEXT NOT NULL DEFAULT 'PENDING',
    decision TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS search_audits (
    batch_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    speed_stage TEXT NOT NULL,
    provider TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    threshold_config_version TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    rejection_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- ============ P2: dashboard / memory deepening / difficulty history ============

CREATE TABLE IF NOT EXISTS memory_target_occurrences (
    occurrence_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    target TEXT NOT NULL,
    material_id TEXT NOT NULL,
    sentence_id TEXT NOT NULL,
    part_no INTEGER,
    source_kind TEXT NOT NULL DEFAULT 'DICTATION',
    created_at TEXT NOT NULL,
    UNIQUE(scope_id, sentence_id, target)
);

CREATE TABLE IF NOT EXISTS memory_recognition_episodes (
    episode_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    target TEXT NOT NULL,
    occurrence_id TEXT NOT NULL REFERENCES memory_target_occurrences(occurrence_id),
    sentence_id TEXT NOT NULL,
    first_exact_listen_count INTEGER,
    revealed INTEGER NOT NULL DEFAULT 0,
    hint_used INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    episode_date TEXT NOT NULL,
    backfilled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(scope_id, sentence_id, target, episode_date)
);

CREATE TABLE IF NOT EXISTS memory_threshold_configs (
    scope_id TEXT PRIMARY KEY,
    short_days INTEGER NOT NULL,
    long_days INTEGER NOT NULL,
    min_episodes INTEGER NOT NULL,
    min_dates INTEGER NOT NULL,
    config_version TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_preferences (
    scope_id TEXT PRIMARY KEY,
    suggestions_enabled INTEGER NOT NULL DEFAULT 1,
    batch_paused INTEGER NOT NULL DEFAULT 0,
    global_paused INTEGER NOT NULL DEFAULT 0,
    snoozed_until TEXT,
    frequency_days INTEGER NOT NULL DEFAULT 7,
    per_target_disabled_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_suggestions (
    suggestion_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    target TEXT NOT NULL,
    trigger_evidence_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    snoozed_until TEXT,
    UNIQUE(scope_id, target, created_at)
);

CREATE TABLE IF NOT EXISTS difficulty_events (
    event_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    stage_before TEXT,
    stage_after TEXT,
    source_record_ids_json TEXT NOT NULL DEFAULT '[]',
    actor TEXT NOT NULL DEFAULT 'SYSTEM',
    reason TEXT,
    policy_version TEXT NOT NULL DEFAULT '1.0',
    created_at TEXT NOT NULL,
    UNIQUE(scope_id, event_type, occurred_at)
);

CREATE TABLE IF NOT EXISTS backfill_audits (
    audit_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL DEFAULT 'default',
    source_record_ids_json TEXT NOT NULL,
    fields_derived_json TEXT NOT NULL,
    source_schema_version TEXT NOT NULL,
    metric_version TEXT NOT NULL,
    reliability TEXT NOT NULL,
    unavailable_reasons_json TEXT NOT NULL DEFAULT '{}',
    backfilled_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_occurrences_target ON memory_target_occurrences(scope_id, target, created_at);
CREATE INDEX IF NOT EXISTS idx_episodes_target ON memory_recognition_episodes(scope_id, target, episode_date);
CREATE INDEX IF NOT EXISTS idx_difficulty_events_scope ON difficulty_events(scope_id, occurred_at);

CREATE INDEX IF NOT EXISTS idx_candidates_batch ON material_candidates(search_batch_id, candidate_status);
CREATE INDEX IF NOT EXISTS idx_gate_records_week ON weekly_gate_records(scope_id, created_at);

CREATE INDEX IF NOT EXISTS idx_sentences_material_part
    ON sentences(material_id, part_no, sequence_no);
CREATE INDEX IF NOT EXISTS idx_dictation_attempts_sentence
    ON dictation_attempts(sentence_id, attempt_number);
CREATE INDEX IF NOT EXISTS idx_comprehension_checks_material
    ON comprehension_checks(material_id, phase, created_at);
CREATE INDEX IF NOT EXISTS idx_reading_attempts_material
    ON reading_attempts(material_id, scope, part_no, attempt_number);
CREATE INDEX IF NOT EXISTS idx_time_logs_session
    ON training_time_logs(session_id, start_time);
