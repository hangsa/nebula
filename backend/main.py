"""FastAPI entry — nebula API.

Owns the application-level state for v1.9: AutopilotLoopService and the
stage4 executor live on `app.state` so the autopilot API can spawn/cancel
runner tasks. On startup we recover any sessions that were running when the
previous process died (spec §4E).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Root logger at INFO so module-level logger.info(...) (e.g.
# b3_engine.meta_decompose/decompose elapsed=) is captured. Must run before
# any other logging.getLogger() so the StreamHandler is installed.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from backend.api import (
    project, stage1_concept, stage2_world_char, stage2_map, stage3_outline, stage4_writing,
    stage5_diagnosis, stage6_export, style_extractor, conductor, storyos,
    settings_api, creative_diverge, growth_workshop, style_sandbox, autopilot,
    stage4_fact_guard, prompt_plaza, prompt_defaults,
    llm_config_api, creative_divergence, b3_routes, creative_dimensions,
)
from backend.api import genres as genres_api
from backend.api.autopilot import broadcaster as autopilot_broadcaster
from backend.config import settings
from backend.conductor.autopilot_loop import AutopilotLoopService
from backend.conductor.stage4_async_executor import AsyncStage4Executor
from backend.conductor.autopilot_session import AutopilotSessionManager
from backend.genres.catalog import load_seed
from backend.services.creative_dimensions_store import CreativeDimensionsStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.loop_service = AutopilotLoopService()
    # CRITICAL: the executor must publish scene chunks to the SAME broadcaster
    # instance that the /chapter-stream SSE endpoint subscribes to. Both live
    # in backend.api.autopilot (module-level singleton) and the SSE endpoint
    # reads from there — instantiating the executor without this broadcaster
    # gives it a private, empty broadcaster that silently drops every event,
    # so the cockpit shows "等待 AI 输出第一个字" forever. Found and fixed
    # 2026-07-17 after proj_cc4ca4ae produced no chunks despite /session/start
    # succeeding and the runner draining the queue.
    app.state.stage4_executor = AsyncStage4Executor(
        projects_dir=settings.projects_dir,
        broadcaster=autopilot_broadcaster,
    )
    # Crash recovery: re-spawn runners for sessions that were 'running' when the
    # previous process exited. Stale sessions (>30s without heartbeat) are
    # downgraded to paused (spec §5 row 9 + spec L287). Forward the same
    # broadcaster so recovered sessions publish to the live SSE channel too.
    await app.state.loop_service.recover_running_sessions(
        settings.projects_dir, broadcaster=autopilot_broadcaster,
    )
    # 全局创作维度 store (subject/tone/style)。JSON 文件路径：优先
    # settings.creative_dimensions_path，回退到 <projects_dir>/../config/creative_dimensions.json
    from pathlib import Path as _Path
    _dim_path_str = settings.creative_dimensions_path
    if _dim_path_str:
        _dim_path = _Path(_dim_path_str)
    else:
        _dim_path = _Path(settings.projects_dir).parent / "config" / "creative_dimensions.json"
    app.state.creative_dimensions_store = CreativeDimensionsStore(
        store_path=_dim_path,
        seed_loader=load_seed,
    )
    app.state.creative_dimensions_store.load()
    from backend.creative_os.b3_engine import _register_dimensions_store
    _register_dimensions_store(app.state.creative_dimensions_store)
    try:
        yield
    finally:
        # On shutdown, cancel any in-flight runners.
        for pid in list(app.state.loop_service._tasks.keys()):
            await app.state.loop_service.cancel(pid)


app = FastAPI(
    title="nebula API",
    description="AI-Powered Creative Narrative Operating System",
    version="0.1.0-mvp",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(creative_divergence.DeprecationHeadersMiddleware)

app.include_router(project.router)
app.include_router(conductor.router)
app.include_router(stage1_concept.router)
app.include_router(stage2_world_char.router)
app.include_router(stage2_map.router)
app.include_router(stage3_outline.router)
app.include_router(stage4_writing.router)
app.include_router(stage4_fact_guard.router)
app.include_router(storyos.router)
app.include_router(stage5_diagnosis.router)
app.include_router(stage6_export.router)
app.include_router(style_extractor.router)
app.include_router(settings_api.router)
app.include_router(creative_diverge.router)
app.include_router(growth_workshop.router)
app.include_router(style_sandbox.router)
app.include_router(stage3_outline.branch_router)
app.include_router(autopilot.router)
app.include_router(prompt_plaza.router)
app.include_router(prompt_defaults.router)
app.include_router(llm_config_api.router)
app.include_router(genres_api.router)
app.include_router(creative_divergence.router)
app.include_router(b3_routes.router)
app.include_router(creative_dimensions.router)

if settings.enable_canvas_v2:
    from backend.api.v2_canvas import router as v2_canvas_router
    app.include_router(v2_canvas_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "code": "INTERNAL_ERROR",
            "message": str(exc),
            "detail": {},
        },
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}