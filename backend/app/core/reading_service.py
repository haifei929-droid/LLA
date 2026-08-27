"""Reading scoring service: measure, then record, then gate (Spec 25).

The service owns the deterministic chain: reference analysis + user recording
analysis -> Rule Engine -> reading_attempts row. It never decides training
flow itself; the state machine (TrainingEventService) consumes the recorded
overall_pass when completing a Part or the full assessment.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from uuid import uuid4

from app.adapters.audio import AudioAnalyzer, WaveAudioAnalyzer
from app.config import Settings
from app.core.progress import utc_now
from app.core.reading_scoring import ReadingRuleEngine, ReadingThresholds, ReadingScore
from app.core.states import MaterialState, TransitionError
from app.db.connection import Database

_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


class ReadingService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        analyzer: AudioAnalyzer | None = None,
        thresholds: ReadingThresholds | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.analyzer = analyzer or WaveAudioAnalyzer()
        if thresholds is None:
            thresholds = ReadingThresholds(
                speed_tolerance_pass=settings.reading_speed_tolerance_pass,
                speed_tolerance_close=settings.reading_speed_tolerance_close,
                pause_tolerance_pass=settings.reading_pause_tolerance_pass,
                pause_tolerance_close=settings.reading_pause_tolerance_close,
                stress_tolerance_pass=settings.reading_stress_tolerance_pass,
                stress_tolerance_close=settings.reading_stress_tolerance_close,
            )
        self.rule_engine = ReadingRuleEngine(thresholds)

    def save_recording(self, filename: str, content_base64: str) -> Path:
        if not filename.strip() or not content_base64.strip():
            raise ValueError("filename and content are required")
        safe_name = _SAFE_FILENAME.sub("_", Path(filename).name)
        if not safe_name:
            raise ValueError("filename must contain valid characters")
        path = self.settings.recordings_dir / f"{uuid4()}-{safe_name}"
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("content must be valid base64") from exc
        if not content:
            raise ValueError("recording content is empty")
        path.write_bytes(content)
        return path

    def score(
        self,
        *,
        material_id: str,
        scope: str,
        part_no: int | None,
        recording_path: Path,
    ) -> dict[str, object]:
        if scope not in ("PART", "FULL"):
            raise ValueError("scope must be PART or FULL")
        if scope == "PART" and part_no not in (1, 2, 3):
            raise ValueError("part_no must be 1, 2, or 3 for PART scope")
        if scope == "FULL" and part_no is not None:
            raise ValueError("part_no must be null for FULL scope")
        if not recording_path.is_file():
            raise ValueError("recording file does not exist")

        with self.database.connect() as connection:
            material = connection.execute(
                "SELECT audio_path FROM materials WHERE material_id = ?", (material_id,)
            ).fetchone()
            if material is None:
                raise KeyError(f"No material exists for {material_id}")
            progress = connection.execute(
                "SELECT current_state FROM training_progress WHERE material_id = ?", (material_id,)
            ).fetchone()
            if progress is None:
                raise KeyError(f"No progress exists for material {material_id}")
            expected_state = (
                MaterialState.READING_AVAILABLE.value if scope == "PART" else MaterialState.FULL_READING_ASSESSMENT.value
            )
            if progress["current_state"] != expected_state:
                raise TransitionError(f"{scope} scoring is not available in state {progress['current_state']}")

        reference_path = Path(str(material["audio_path"]))
        if not reference_path.is_absolute():
            reference_path = self.settings.project_root / reference_path
        if not reference_path.is_file():
            raise ValueError(f"Reference audio is not available: {reference_path}")

        reference = self.analyzer.analyze(str(reference_path))
        user = self.analyzer.analyze(str(recording_path))
        score: ReadingScore = self.rule_engine.score(reference, user)

        with self.database.connect() as connection:
            attempt_number = connection.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_attempt
                  FROM reading_attempts
                 WHERE material_id = ? AND scope = ? AND (part_no IS ? OR part_no = ?)
                """,
                (material_id, scope, part_no, part_no),
            ).fetchone()["next_attempt"]
            connection.execute(
                """
                INSERT INTO reading_attempts(
                    attempt_id, material_id, scope, part_no, attempt_number,
                    reference_duration, user_duration, speed_result, pause_result,
                    stress_result, overall_pass, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    material_id,
                    scope,
                    part_no,
                    attempt_number,
                    score.reference_duration,
                    score.user_duration,
                    score.speed,
                    score.pause,
                    score.stress,
                    int(score.overall_pass),
                    utc_now(),
                ),
            )
        return {
            "material_id": material_id,
            "scope": scope,
            "part_no": part_no,
            "attempt_number": attempt_number,
            "reference_duration": score.reference_duration,
            "user_duration": score.user_duration,
            "speed": score.speed,
            "pause": score.pause,
            "stress": score.stress,
            "overall_pass": score.overall_pass,
            "reference_pause_count": score.reference_pause_count,
            "user_pause_count": score.user_pause_count,
        }
