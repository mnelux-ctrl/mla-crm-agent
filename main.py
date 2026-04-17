"""
main.py — FastAPI entrypoint for mla-crm-agent (6th MLA Railway service).

Wires together:
  - API routes: /api/crm/*, /api/coo/*, /api/internal/*
  - Slack Bolt handlers for campaign approval buttons
  - APScheduler for rate-limited per-recipient sends
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

# Make relative imports work under both `uvicorn main:app` and `python main.py`
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from api.campaigns import router as campaigns_router
from api.segments import router as segments_router
from api.templates import router as templates_router
from api.coo import router as coo_router
from api.internal import router as internal_router
from sending.runner import get_scheduler, shutdown_scheduler
from slack import callbacks as slack_callbacks
from slack import dm_handler as slack_dm
from state.redis_client import ping as redis_ping

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mla-crm-agent")


# ── Lifespan (startup/shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("mla-crm-agent starting up...")
    config.validate_all()
    # Warm Redis
    if redis_ping():
        logger.info("Redis connected")
    else:
        logger.warning("Redis ping failed — campaign state will fail")
    # Start APScheduler
    get_scheduler()
    yield
    logger.info("mla-crm-agent shutting down...")
    shutdown_scheduler()


app = FastAPI(
    title="MLA CRM Agent",
    version="0.1.0",
    description="Smart outreach + segmentation + rate-limited send orchestrator for MLA.",
    lifespan=lifespan,
)

# CORS — open for future frontend on separate origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten for prod once frontend has a domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routers ─────────────────────────────────────────────────────────────────

app.include_router(campaigns_router)
app.include_router(segments_router)
app.include_router(templates_router)
app.include_router(coo_router)
app.include_router(internal_router)


# ── Slack Bolt handlers (interactivity: button clicks) ──────────────────────

if config.SLACK_BOT_TOKEN and config.SLACK_SIGNING_SECRET:
    slack_app = AsyncApp(
        token=config.SLACK_BOT_TOKEN,
        signing_secret=config.SLACK_SIGNING_SECRET,
    )

    @slack_app.action("crm_approve")
    async def _approve(ack, body, client):
        await slack_callbacks.handle_approve(body, ack, client)

    @slack_app.action("crm_cancel")
    async def _cancel(ack, body, client):
        await slack_callbacks.handle_cancel(body, ack, client)

    @slack_app.action("crm_edit_template")
    async def _edit_tmpl(ack, body, client):
        await slack_callbacks.handle_edit_template(body, ack, client)

    @slack_app.action("crm_edit_list")
    async def _edit_list(ack, body, client):
        await slack_callbacks.handle_edit_list(body, ack, client)

    # DM / mention handler — Stefan talks to the bot in plain language
    @slack_app.event("message")
    async def _on_message(event, client):
        # Only handle direct messages (channel_type='im')
        if event.get("channel_type") == "im":
            await slack_dm.handle_dm(event, client)

    @slack_app.event("app_mention")
    async def _on_mention(event, client):
        await slack_dm.handle_dm(event, client)

    slack_handler = AsyncSlackRequestHandler(slack_app)

    @app.post("/slack/interactions")
    async def slack_interactions(req: Request):
        return await slack_handler.handle(req)

    @app.post("/slack/events")
    async def slack_events(req: Request):
        return await slack_handler.handle(req)

    logger.info("Slack Bolt handlers registered (DM + interactions + events)")
else:
    logger.warning(
        "SLACK_BOT_TOKEN / SLACK_SIGNING_SECRET not set — Slack interactivity disabled. "
        "Campaigns can still be approved via POST /api/crm/campaigns/{id}/approve."
    )


# ── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "mla-crm-agent",
        "version": "0.1.0",
        "redis": redis_ping(),
        "env": config.ENV,
    }


@app.get("/")
async def root():
    return {
        "service": "mla-crm-agent",
        "status": "running",
        "docs": "/docs",
    }


# ── Global exception handler ────────────────────────────────────────────────

@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": str(exc), "error_code": "internal_error"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=True)
