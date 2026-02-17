"""
mbot.online — FastAPI web application factory.

Provides:
  - Client onboarding + KYC capture
  - API key submission (encrypted)
  - Performance dashboard
  - Commission history + PDF download
  - Admin: kill switch, health check
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web.routers import admin, auth, clients, commissions, positions

log = logging.getLogger("web")


def create_app() -> FastAPI:
    app = FastAPI(
        title="xmbot — mbot.online",
        description="Crypto trading bot management platform",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — restrict in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(auth.router)
    app.include_router(clients.router)
    app.include_router(positions.router)
    app.include_router(commissions.router)
    app.include_router(admin.router)

    @app.get("/", tags=["meta"])
    def root() -> dict:
        return {"service": "xmbot", "status": "online"}

    return app


app = create_app()
