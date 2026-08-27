from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Runtime configuration kept small and explicit for the local-first P0."""

    project_root: Path = PROJECT_ROOT
    database_path: Path = PROJECT_ROOT / "data" / "language_training.sqlite3"
    materials_dir: Path = PROJECT_ROOT / "data" / "materials"
    recordings_dir: Path = PROJECT_ROOT / "data" / "recordings"
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"
    app_name: str = "Language Training Agent"
    environment: str = "development"
    #: Weekly window policy for stats()["weekly_learning_seconds"]:
    #: "calendar" = ISO week starting Monday, "rolling7" = trailing 7 days.
    weekly_window: str = "calendar"
    #: Reading Rule Engine tolerances (Spec 33: calibrate with real samples).
    reading_speed_tolerance_pass: float = 0.15
    reading_speed_tolerance_close: float = 0.30
    reading_pause_tolerance_pass: int = 2
    reading_pause_tolerance_close: int = 4
    reading_stress_tolerance_pass: float = 0.25
    reading_stress_tolerance_close: float = 0.45
    #: Weekly assessment tuning (Spec 14/15; calibrate later).
    weekly_dictation_pass_threshold: float = 80.0
    weekly_test_sentence_count: int = 6
    reinforcement_max_sentences: int = 5
    #: Audio-quality bar for searched material (user requirement: 清晰音质).
    audio_quality_min_sample_rate: int = 16000
    audio_quality_min_snr_db: float = 15.0
    audio_quality_max_silence_ratio: float = 0.55
    audio_quality_min_duration_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(os.getenv("LTA_PROJECT_ROOT", str(PROJECT_ROOT))).resolve()
        data_root = project_root / "data"
        return cls(
            project_root=project_root,
            database_path=Path(os.getenv("LTA_DATABASE_PATH", str(data_root / "language_training.sqlite3"))),
            materials_dir=Path(os.getenv("LTA_MATERIALS_DIR", str(data_root / "materials"))),
            recordings_dir=Path(os.getenv("LTA_RECORDINGS_DIR", str(data_root / "recordings"))),
            processed_dir=Path(os.getenv("LTA_PROCESSED_DIR", str(data_root / "processed"))),
            app_name=os.getenv("LTA_APP_NAME", "Language Training Agent"),
            environment=os.getenv("LTA_ENVIRONMENT", "development"),
            weekly_window=os.getenv("LTA_WEEKLY_WINDOW", "calendar"),
            reading_speed_tolerance_pass=float(os.getenv("LTA_READING_SPEED_PASS", "0.15")),
            reading_speed_tolerance_close=float(os.getenv("LTA_READING_SPEED_CLOSE", "0.30")),
            reading_pause_tolerance_pass=int(os.getenv("LTA_READING_PAUSE_PASS", "2")),
            reading_pause_tolerance_close=int(os.getenv("LTA_READING_PAUSE_CLOSE", "4")),
            reading_stress_tolerance_pass=float(os.getenv("LTA_READING_STRESS_PASS", "0.25")),
            reading_stress_tolerance_close=float(os.getenv("LTA_READING_STRESS_CLOSE", "0.45")),
            weekly_dictation_pass_threshold=float(os.getenv("LTA_WEEKLY_DICTATION_PASS", "80")),
            weekly_test_sentence_count=int(os.getenv("LTA_WEEKLY_TEST_SENTENCES", "6")),
            reinforcement_max_sentences=int(os.getenv("LTA_REINFORCEMENT_MAX_SENTENCES", "5")),
            audio_quality_min_sample_rate=int(os.getenv("LTA_AUDIO_QUALITY_SAMPLE_RATE", "16000")),
            audio_quality_min_snr_db=float(os.getenv("LTA_AUDIO_QUALITY_SNR_DB", "15")),
            audio_quality_max_silence_ratio=float(os.getenv("LTA_AUDIO_QUALITY_SILENCE", "0.55")),
            audio_quality_min_duration_seconds=float(os.getenv("LTA_AUDIO_QUALITY_MIN_DURATION", "60")),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.materials_dir.mkdir(parents=True, exist_ok=True)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

