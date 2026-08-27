from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings
from app.core.dictation_service import DictationService
from app.core.materials import MaterialStore
from app.core.progress import TrainingProgressStore
from app.core.training_events import TrainingEventService
from app.core.learning_time import LearningTimeService
from app.core.material_search import MaterialSearchService
from app.core.reading_service import ReadingService
from app.core.weekly import WeeklyAssessmentService
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
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(router)
