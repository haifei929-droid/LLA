"""Homepage recommendation ladder (P0 Spec 26.2), read-only.

Ranks exactly one next action: reinforcement > dictation resume > second
listen > comprehension retest > reading part > full reading assessment >
next material > import. Training Core remains authoritative; this service
only reads material progress, materials, and the latest weekly assessment.
"""

from __future__ import annotations

import json

from app.core.states import MaterialState, WeeklyState
from app.db.connection import Database

_COMPLETED = {MaterialState.LISTENING_COMPLETED, MaterialState.FULLY_COMPLETED}
_REINFORCING = {
    WeeklyState.REINFORCEMENT_REQUIRED,
    WeeklyState.REINFORCEMENT,
    WeeklyState.TARGETED_RETEST,
}
_TESTING = {
    WeeklyState.WEEKLY_ASSESSMENT_READY,
    WeeklyState.DICTATION_WEEKLY_TEST,
    WeeklyState.READING_WEEKLY_TEST,
}
_RESUME_INTERLUDE = {
    MaterialState.MATERIAL_CREATED,
    MaterialState.READY_FIRST_LISTEN,
    MaterialState.FIRST_FULL_LISTEN,
    MaterialState.FIRST_COMPREHENSION_CHECK,
}


class HomeRecommendationService:
    """Deterministic, side-effect-free homepage recommendation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def read(self) -> dict[str, object]:
        with self.database.connect() as connection:
            materials = connection.execute(
                """
                SELECT m.material_id, m.title, p.current_state, p.dictation_part_status,
                       p.reading_part_status
                  FROM materials m
                  JOIN training_progress p ON p.material_id = m.material_id
                 WHERE m.prepare_status = 'READY'
                 ORDER BY p.updated_at DESC, m.material_id
                """
            ).fetchall()
            week = connection.execute(
                """
                SELECT week_id, gate_status
                  FROM weekly_assessments
                 ORDER BY created_at DESC, week_id DESC
                 LIMIT 1
                """
            ).fetchone()
        return self._recommend(materials, week)

    @staticmethod
    def _recommend(materials, week) -> dict[str, object]:
        # R1: weekly gate unfinished or failed outranks everything.
        if week is not None and week["gate_status"] != WeeklyState.WEEKLY_GATE_PASS.value:
            gate = week["gate_status"]
            week_detail = f"第 {week['week_id']} 周测试未完成或未通过，需先完成周测与强化"
            if gate in _REINFORCING:
                return {
                    "priority": "R1",
                    "title": "周测未通过，先完成强化",
                    "detail": week_detail,
                    "cta": "进入强化训练",
                    "target_view": "weekly",
                    "material_id": None,
                    "week_id": week["week_id"],
                    "tone": "danger",
                }
            return {
                "priority": "R1",
                "title": "本周周测尚未完成",
                "detail": week_detail,
                "cta": "继续周测",
                "target_view": "weekly",
                "material_id": None,
                "week_id": week["week_id"],
                "tone": "danger",
            }

        if not materials:
            return {
                "priority": "R7",
                "title": "还没有训练素材",
                "detail": "导入一篇素材即可开始盲听与听写训练",
                "cta": "导入素材",
                "target_view": "materials",
                "material_id": None,
                "week_id": None,
                "tone": "default",
            }

        # R0: every material completed (a failed gate already returned R1).
        if all(MaterialState(row["current_state"]) in _COMPLETED for row in materials):
            return {
                "priority": None,
                "title": None,
                "detail": None,
                "cta": None,
                "target_view": None,
                "material_id": None,
                "week_id": None,
                "tone": "default",
            }

        # R2-R6: most recently updated material first (updated_at DESC).
        for row in materials:
            state = MaterialState(row["current_state"])
            if state in (MaterialState.DICTATION_PART_1, MaterialState.DICTATION_PART_2, MaterialState.DICTATION_PART_3):
                return {
                    "priority": "R2",
                    "title": f"继续听写 Part {state.value[-1]}",
                    "detail": row["title"],
                    "cta": "继续听写",
                    "target_view": "training",
                    "material_id": row["material_id"],
                    "week_id": None,
                    "tone": "default",
                }
            if state == MaterialState.SECOND_FULL_LISTEN:
                return {
                    "priority": "R3",
                    "title": "继续二次复听",
                    "detail": row["title"],
                    "cta": "继续复听",
                    "target_view": "training",
                    "material_id": row["material_id"],
                    "week_id": None,
                    "tone": "default",
                }
            if state == MaterialState.SECOND_COMPREHENSION_CHECK:
                return {
                    "priority": "R3",
                    "title": "继续理解复测",
                    "detail": row["title"],
                    "cta": "继续复测",
                    "target_view": "training",
                    "material_id": row["material_id"],
                    "week_id": None,
                    "tone": "default",
                }
            if state == MaterialState.READING_AVAILABLE:
                reading = json.loads(row["reading_part_status"])
                part = next((n for n in (1, 2, 3) if not reading.get(str(n))), 1)
                return {
                    "priority": "R4",
                    "title": f"继续朗读 Part {part}",
                    "detail": row["title"],
                    "cta": "继续朗读",
                    "target_view": "training",
                    "material_id": row["material_id"],
                    "week_id": None,
                    "tone": "default",
                }
            if state == MaterialState.FULL_READING_ASSESSMENT:
                return {
                    "priority": "R5",
                    "title": "全文朗读验收",
                    "detail": row["title"],
                    "cta": "开始全文验收",
                    "target_view": "training",
                    "material_id": row["material_id"],
                    "week_id": None,
                    "tone": "default",
                }
            if state in _COMPLETED:
                return {
                    "priority": "R6",
                    "title": "当前素材已完成",
                    "detail": row["title"],
                    "cta": "获取下一篇素材",
                    "target_view": "training",
                    "material_id": row["material_id"],
                    "week_id": None,
                    "tone": "default",
                }
            if state in _RESUME_INTERLUDE:
                return {
                    "priority": "R_CONTINUE",
                    "title": "继续盲听与理解检查",
                    "detail": row["title"],
                    "cta": "继续训练",
                    "target_view": "training",
                    "material_id": row["material_id"],
                    "week_id": None,
                    "tone": "default",
                }
        # Unreachable: a non-empty non-completed library always matches above.
        raise AssertionError("unmatched material state in recommendation ladder")
