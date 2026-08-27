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
