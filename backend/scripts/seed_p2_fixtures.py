"""P2 acceptance fixture generator (LLA P2 Independent Acceptance Test 2.2).

Builds a clean SQLite database whose content covers every P2 acceptance
scenario, then writes a manifest documenting every record's id, timestamp,
expected category, and inclusion/exclusion rule so an independent reviewer
can recompute results without reading implementation code.

Usage:
    python backend/scripts/seed_p2_fixtures.py [--db PATH] [--empty]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import Settings  # noqa: E402
from app.core.dictation_service import DictationService  # noqa: E402
from app.core.difficulty_progression import DifficultyProgressionService  # noqa: E402
from app.core.materials import MaterialStore  # noqa: E402
from app.core.training_events import TrainingEventService  # noqa: E402
from app.core.weekly import WeeklyAssessmentService  # noqa: E402
from app.db.connection import Database  # noqa: E402
from app.preprocess.material import MaterialPreprocessor, TimestampedSentence  # noqa: E402

MATERIALS = [
    {
        "material_id": "fx-m1",
        "title": "Fixture material one",
        "sentences": [
            "The lazy fox sleeps all day near the river.",
            "She sells seashells by the seashore every morning.",
            "He could not find the way back home tonight.",
        ],
    },
    {
        "material_id": "fx-m2",
        "title": "Fixture material two",
        "sentences": [
            "They watched the lazy clouds move across the sky.",
            "We bought fresh seashells at the morning market.",
            "It was raining heavily when the train arrived.",
        ],
    },
    {
        "material_id": "fx-m3",
        "title": "Fixture material three",
        "sentences": [
            "A lazy dog slept under the old wooden bridge.",
            "The children collected seashells on the quiet beach.",
            "Everyone enjoyed the party very much last night.",
        ],
    },
    {
        "material_id": "fx-m4",
        "title": "Fixture material four",
        "sentences": [
            "The river rose slowly after three days of rain.",
            "Farmers worked in the fields until the evening light.",
            "Birds returned to the trees as the sun went down.",
        ],
    },
]
FIRST_BANDS = ["<30%", "30–50%", "50–70%", ">70%"]
NOW = datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC)


def create_material(database: Database, spec: dict, settings: Settings) -> None:
    timestamped = [
        TimestampedSentence(text, index * 10.0, (index + 1) * 10.0)
        for index, text in enumerate(spec["sentences"])
    ]
    material = MaterialPreprocessor().process(
        material_id=spec["material_id"], title=spec["title"],
        audio_path=f"data/materials/{spec['material_id']}.wav",
        transcript=" ".join(spec["sentences"]), timestamped_sentences=timestamped,
    )
    MaterialStore(database).create(material)


def drive_to_dictation(database: Database, events: TrainingEventService, material_id: str, band: str) -> None:
    events.complete_first_listen(material_id)
    events.submit_comprehension(
        material_id=material_id, phase="FIRST", self_rating=band, summary="Fixture first check.",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/p2_fixtures.sqlite3")
    parser.add_argument("--empty", action="store_true", help="only initialize the schema")
    args = parser.parse_args()

    settings = Settings(project_root=Path(__file__).resolve().parents[2], database_path=Path(args.db))
    database = Database(settings)
    database.initialize()
    if args.empty:
        print(json.dumps({"db": str(settings.database_path), "empty": True}))
        return

    events = TrainingEventService(database)
    dictation = DictationService(database)
    weekly = WeeklyAssessmentService(database, settings)
    difficulty = DifficultyProgressionService(database, weekly)

    manifest = {"materials": [], "comprehension": [], "dictation": [], "weekly": [], "gate": []}

    # --- 4 prepared materials, one FIRST comprehension per band ---
    for spec, band in zip(MATERIALS, FIRST_BANDS):
        create_material(database, spec, settings)
        drive_to_dictation(database, events, spec["material_id"], band)
        manifest["materials"].append({"material_id": spec["material_id"], "sentences": len(spec["sentences"])})
        manifest["comprehension"].append({"material_id": spec["material_id"], "phase": "FIRST", "raw_band": band})

    def submit(material_id: str, sentence_no: int, text: str, listen_count: int, hint: int = 0, revealed: bool = False) -> None:
        dictation.submit(
            material_id=material_id,
            sentence_id=f"{material_id}-sentence-{sentence_no:03d}",
            user_text=text, listen_count=listen_count, hint_level=hint, revealed=revealed,
        )

    def advance_part(material_id: str, part: int) -> None:
        events.complete_dictation_part(material_id, part)

    # --- ordinary dictation episodes covering every P2-2-01/02 scenario ---
    # 3-sentence materials split one sentence per Part; each Part must be
    # completed before the next unlocks.
    # fx-m1: Part 1 first-listen success ("lazy"); Part 2 multi-listen
    # success ("seashore"); Part 3 Hint-before-correct ("could").
    submit("fx-m1", 1, MATERIALS[0]["sentences"][0], 1)
    advance_part("fx-m1", 1)
    submit("fx-m1", 2, MATERIALS[0]["sentences"][1].replace("seashore", "sleepy"), 2)
    submit("fx-m1", 2, MATERIALS[0]["sentences"][1], 5)
    advance_part("fx-m1", 2)
    submit("fx-m1", 3, MATERIALS[0]["sentences"][2].replace("could", "would"), 3, hint=1)
    submit("fx-m1", 3, MATERIALS[0]["sentences"][2], 6, hint=1)
    advance_part("fx-m1", 3)
    # fx-m2: Part 1 Reveal-only ("lazy"); Part 2 spelling-only ("market"->
    # "marget") plus a misheard "seashells"->"mussels"; Part 3 plain success.
    submit("fx-m2", 1, "", 2, revealed=True)
    submit("fx-m2", 1, MATERIALS[1]["sentences"][0], 4)
    advance_part("fx-m2", 1)
    submit("fx-m2", 2, MATERIALS[1]["sentences"][1].replace("market", "marget"), 2)
    submit("fx-m2", 2, MATERIALS[1]["sentences"][1].replace("seashells", "mussels"), 3)
    submit("fx-m2", 2, MATERIALS[1]["sentences"][1], 4)
    advance_part("fx-m2", 2)
    submit("fx-m2", 3, MATERIALS[1]["sentences"][2], 2)
    advance_part("fx-m2", 3)
    # fx-m3: cross-material repeats of "lazy" (Part 1) and "seashells"
    # (Part 2, misheard as "mussels").
    submit("fx-m3", 1, MATERIALS[2]["sentences"][0].replace("lazy", "sleepy"), 2)
    submit("fx-m3", 1, MATERIALS[2]["sentences"][0], 4)
    advance_part("fx-m3", 1)
    submit("fx-m3", 2, MATERIALS[2]["sentences"][1].replace("seashells", "mussels"), 3)
    submit("fx-m3", 2, MATERIALS[2]["sentences"][1], 4)
    advance_part("fx-m3", 2)
    submit("fx-m3", 3, MATERIALS[2]["sentences"][2], 1)
    advance_part("fx-m3", 3)
    manifest["dictation"] = [
        # First-listen exact answers produce no difficulty episode by design
        # (no listening error exists), so expected_inclusion is False.
        {"scenario": "first_listen_success", "material": "fx-m1", "sentence": 1, "target": "lazy", "expected_first_correct": None, "expected_inclusion": False},
        {"scenario": "multi_listen_success", "material": "fx-m1", "sentence": 2, "target": "seashore", "expected_first_correct": 5},
        {"scenario": "hint_before_correct", "material": "fx-m1", "sentence": 3, "target": "could", "expected_first_correct": None, "hint_count": 2},
        {"scenario": "reveal_only", "material": "fx-m2", "sentence": 1, "target": "lazy", "expected_first_correct": None, "reveal_count": 1},
        {"scenario": "spelling_only", "material": "fx-m2", "sentence": 2, "target": "market", "expected_inclusion": False},
        {"scenario": "cross_material_1", "material": "fx-m3", "sentence": 1, "target": "lazy", "expected_first_correct": 4},
        {"scenario": "cross_material_2", "material": "fx-m3", "sentence": 2, "target": "seashells", "expected_first_correct": 4},
    ]

    # --- weekly test with reinforcement/retest path (P2-1-04) ---
    weekly.create(week_id="FX-W1", period_start="2026-08-03", period_end="2026-08-09", dictation_required=True, reading_required=False)
    items = weekly.create_test_items("FX-W1", count=3)
    for item in items:
        weekly.submit_test_dictation("FX-W1", item["item_id"], "wrong", listen_count=1)
    failed = weekly.get("FX-W1")
    reinforcement = weekly.start_reinforcement("FX-W1")
    for item in reinforcement["reinforcement_items"]:
        weekly.submit_reinforcement_dictation("FX-W1", item["item_id"], item["text"], listen_count=1)
    weekly.confirm_retest("FX-W1")
    manifest["weekly"].append(
        {
            "week": "FX-W1",
            "initial_dictation_score": failed["dictation_score"],
            "gate_after_fail": failed["gate_status"],
            "retest_gate": weekly.get("FX-W1")["gate_status"],
            "expected_score_below_80": True,
        }
    )

    # --- weekly gate records: 8 stable passes + one failure (P2-3-02/04) ---
    for index in range(1, 9):
        week = f"FX-G{index}"
        weekly.create(week_id=week, period_start="2026-01-01", period_end="2026-01-07", dictation_required=True, reading_required=False)
        weekly.record_dictation(week, score=90.0, passed=True)
        difficulty.evaluate_weekly_gate("default", week)
    manifest["gate"].append({"scenario": "eight_stable_passes", "expected_consecutive": 8, "expected_eligible": True})
    weekly.create(week_id="FX-FAIL", period_start="2026-01-01", period_end="2026-01-07", dictation_required=True, reading_required=False)
    weekly.record_dictation("FX-FAIL", score=65.0, passed=False)
    difficulty.evaluate_weekly_gate("default", "FX-FAIL")
    manifest["gate"].append({"scenario": "failure_resets_streak", "expected_consecutive": 0})

    # --- reading practice attempts with independent dimensions (P2-1-05) ---
    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO reading_attempts(
                attempt_id, material_id, scope, part_no, attempt_number, user_duration,
                reference_duration, speed_result, pause_result, stress_result, overall_pass, created_at
            ) VALUES (?, 'fx-m4', 'PART', 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (str(uuid4()), 1, 95.0, 100.0, "PASS", "CLOSE", "PASS", 0, "2026-08-01T10:00:00+00:00"),
                (str(uuid4()), 2, 120.0, 100.0, "FAIL", "PASS", "PASS", 0, "2026-08-08T10:00:00+00:00"),
            ],
        )
    manifest["reading"] = [
        {"scenario": "mixed_dimensions", "attempts": 2, "speed": {"PASS": 1, "FAIL": 1}, "pause": {"CLOSE": 1, "PASS": 1}, "stress": {"PASS": 2}},
    ]

    manifest["environment"] = {
        "database": str(settings.database_path),
        "metric_version": "1.0",
        "mapping_version": "1.0",
        "timezone": "UTC",
        "weekly_policy": "calendar",
        "commit": "generated by seed_p2_fixtures.py",
    }
    manifest_path = Path(args.db).with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"db": str(settings.database_path), "manifest": str(manifest_path), "generated": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
