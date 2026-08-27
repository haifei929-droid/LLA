from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import Settings
from app.core.dictation_service import DictationService
from app.core.materials import MaterialStore
from app.core.progress import TrainingProgressStore
from app.core.training_events import TrainingEventService
from app.core.learning_time import LearningTimeService
from app.core.material_candidates import MaterialCandidateService
from app.core.material_preparation import MaterialPreparationService
from app.core.material_search import MaterialSearchService
from app.core.reading_service import ReadingService
from app.core.weekly import WeeklyAssessmentService
from app.core.difficulty_progression import DifficultyProgressionService
from app.core.difficulty_history import DifficultyHistoryService
from app.core.dashboard import DashboardService
from app.core.memory_deepening import MemoryDeepeningService
from app.adapters.voa_material import VOALearningEnglishProvider
from app.adapters.web_material import BBCLearningEnglishProvider
from app.db.connection import Database


settings = Settings.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_directories()
    database = Database(settings)
    database.initialize()
    app.state.settings = settings
    app.state.database = database
    app.state.material_store = MaterialStore(database)
    app.state.progress_store = TrainingProgressStore(database)
    app.state.dictation_service = DictationService(database)
    app.state.training_events = TrainingEventService(database)
    app.state.learning_time = LearningTimeService(database)
    app.state.weekly_assessments = WeeklyAssessmentService(database, settings)
    app.state.reading_service = ReadingService(database, settings)
    # Provider priority: VOA slow English (standard) first, BBC as fallback.
    app.state.material_search = MaterialSearchService(
        database,
        settings,
        providers=[VOALearningEnglishProvider(), BBCLearningEnglishProvider()],
    )
    app.state.material_candidates = MaterialCandidateService(database, settings)
    app.state.difficulty = DifficultyProgressionService(
        database, WeeklyAssessmentService(database, settings)
    )
    app.state.difficulty_history = DifficultyHistoryService(database)
    app.state.difficulty.history = app.state.difficulty_history
    app.state.material_preparation = MaterialPreparationService(
        database, settings, history=app.state.difficulty_history
    )
    app.state.material_search.history = app.state.difficulty_history
    app.state.dashboard = DashboardService(database)
    app.state.memory = MemoryDeepeningService(database)
    yield


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.include_router(router)

# Serve the built frontend from the same process (no file watcher, immune to
# the Vite dev-server crash on Windows). API routes take precedence; the
# static mount is the fallback for everything else.
_frontend_dist = settings.project_root / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
