"""FastAPI application: JSON analytics API + AI analyst + static dashboard."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.orchestrator import AgentOrchestrator, suggested_questions
from app.analytics import AnalyticsService, money
from app.config import Settings, settings
from app.db import db
from app.errors import AppError, RateLimitError

logger = logging.getLogger("atlas")
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

analytics = AnalyticsService(db)
agent = AgentOrchestrator(analytics=analytics, cfg=settings)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=600)


class RateLimiter:
    """Small in-process limiter for the LLM-backed endpoint."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = max(1, limit)
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(self.window - (now - hits[0])))
                raise RateLimitError(
                    f"Too many questions. Try again in about {retry_after} seconds.",
                    details={"retry_after_seconds": retry_after},
                )
            hits.append(now)


limiter = RateLimiter(settings.ask_rate_limit_per_minute)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.ensure_ready()
    logger.info(
        "Atlas BI ready | as_of=%s | llm=%s | provider=%s | model=%s",
        db.as_of,
        settings.llm_mode,
        settings.llm_provider,
        settings.resolved_model if settings.llm_configured else "deterministic",
    )
    yield


app = FastAPI(
    title="Atlas BI",
    version=settings.app_version,
    description=(
        "AI business intelligence for an Indian e-commerce retailer. "
        "Grounded analytics, controlled tool calling and a deterministic fallback."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

STATIC_DIR = settings.static_dir
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "The request was not valid.",
                "details": {"errors": exc.errors()},
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    logger.exception("Unhandled error")
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Something went wrong while processing the request.",
                "details": {},
            }
        },
    )


# ---------------------------------------------------------------------------
# Health and metadata
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    result = db.health()
    result["llm"] = {
        "configured": settings.llm_configured,
        "mode": settings.llm_mode,
        "provider": settings.llm_provider,
        "model": settings.resolved_model if settings.llm_configured else None,
    }
    return result


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    quality = db.quality_report()
    return {
        "app": {
            "name": "Atlas BI",
            "version": settings.app_version,
            "description": "AI business analyst for BharatCart Retail",
        },
        "business": {
            "name": "BharatCart Retail",
            "market": "India",
            "currency": "INR",
            "currency_symbol": "₹",
            "as_of": analytics.as_of.isoformat(),
            "revenue_definition": quality.get(
                "revenue_definition",
                "Net product revenue from completed orders only; shipping and taxes excluded.",
            ),
        },
        "llm": {
            "configured": settings.llm_configured,
            "mode": settings.llm_mode,
            "provider": settings.llm_provider if settings.llm_configured else "offline",
            "model": settings.resolved_model if settings.llm_configured else "deterministic planner",
        },
        "counts": db.counts(),
        "quality": {
            "total_issues": quality.get("total_issues", 0),
            "raw_counts": quality.get("raw_counts", {}),
            "clean_counts": quality.get("clean_counts", {}),
            "checks_passed": sum(1 for c in quality.get("checks", []) if c.get("status") == "pass"),
            "checks_total": len(quality.get("checks", [])),
        },
        "periods": [
            analytics.period(key).to_dict()
            for key in (
                "latest_month", "previous_month", "last_30_days", "last_90_days",
                "last_12_months", "ytd", "all_time",
            )
        ],
        "suggested_questions": suggested_questions(analytics),
    }


@app.get("/api/periods")
def periods() -> dict[str, Any]:
    return {
        "as_of": analytics.as_of.isoformat(),
        "periods": [
            analytics.period(key).to_dict()
            for key in (
                "latest_month", "previous_month", "last_30_days", "last_90_days",
                "last_12_months", "ytd", "all_time",
            )
        ],
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.get("/api/overview")
def overview(period: str = Query("last_12_months")) -> dict[str, Any]:
    data = analytics.overview(period)
    data["llm"] = {
        "configured": settings.llm_configured,
        "mode": settings.llm_mode,
        "provider": settings.llm_provider if settings.llm_configured else "offline",
        "model": settings.resolved_model if settings.llm_configured else "deterministic planner",
    }
    data["suggested_questions"] = suggested_questions(analytics)
    data["headline"] = (
        f"Latest month revenue was {money(data['kpis']['kpis']['revenue']['value'])} "
        f"({data['kpis']['kpis']['revenue']['delta_pct']:+.1f}% vs the previous month)."
    )
    return data


@app.get("/api/kpis")
def kpis(period: str = Query("last_12_months")) -> dict[str, Any]:
    return analytics.kpis(period)


@app.get("/api/charts/monthly-revenue")
def monthly_revenue(months: int = Query(12, ge=3, le=24)) -> dict[str, Any]:
    return analytics.monthly_revenue_chart(months)


@app.get("/api/charts/revenue-by-category")
def revenue_by_category(period: str = Query("last_12_months")) -> dict[str, Any]:
    return analytics.revenue_by_category_chart(period)


@app.get("/api/charts/top-products")
def top_products_chart(
    period: str = Query("last_12_months"), limit: int = Query(8, ge=1, le=20)
) -> dict[str, Any]:
    return analytics.top_products_chart(period, limit)


@app.get("/api/charts/customer-trend")
def customer_trend_chart(months: int = Query(12, ge=3, le=24)) -> dict[str, Any]:
    return analytics.customer_trend_chart(months)


@app.get("/api/charts/payment-mix")
def payment_mix_chart(period: str = Query("last_12_months")) -> dict[str, Any]:
    return analytics.payment_mix_chart(period)


@app.get("/api/charts/city-tier")
def city_tier_chart(period: str = Query("last_12_months")) -> dict[str, Any]:
    return analytics.city_tier_chart(period)


@app.get("/api/charts/channel")
def channel_chart(period: str = Query("last_12_months")) -> dict[str, Any]:
    return analytics.channel_chart(period)


@app.get("/api/products/top")
def top_products(
    period: str = Query("last_12_months"),
    metric: str = Query("revenue", pattern="^(revenue|units)$"),
    limit: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    return {"period": analytics.period(period).to_dict(), "products": analytics.top_products(period, metric, limit)}


@app.get("/api/products/low")
def low_products(
    period: str = Query("last_12_months"), limit: int = Query(5, ge=1, le=20)
) -> dict[str, Any]:
    return {"period": analytics.period(period).to_dict(), "products": analytics.low_performing_products(period, limit)}


@app.get("/api/customers/metrics")
def customer_metrics(inactive_days: int = Query(90, ge=1, le=730)) -> dict[str, Any]:
    return analytics.customer_metrics(inactive_days)


@app.get("/api/customers/inactive")
def inactive_customers(
    inactive_days: int = Query(90, ge=1, le=730), limit: int = Query(10, ge=1, le=50)
) -> dict[str, Any]:
    return analytics.inactive_customers(inactive_days, limit)


@app.get("/api/customers/top")
def top_customers(
    period: str = Query("last_12_months"), limit: int = Query(5, ge=1, le=20)
) -> dict[str, Any]:
    return {"period": analytics.period(period).to_dict(), "customers": analytics.top_customers(period, limit)}


@app.get("/api/payments")
def payments(period: str = Query("last_12_months")) -> dict[str, Any]:
    return {"period": analytics.period(period).to_dict(), "payment_mix": analytics.payment_mix(period)}


@app.get("/api/city-tiers")
def city_tiers(period: str = Query("last_12_months")) -> dict[str, Any]:
    return {"period": analytics.period(period).to_dict(), "city_tiers": analytics.city_tier_mix(period)}


@app.get("/api/channels")
def channels(period: str = Query("last_12_months")) -> dict[str, Any]:
    return {"period": analytics.period(period).to_dict(), "channels": analytics.channel_performance(period)}


@app.get("/api/states")
def states(
    period: str = Query("last_12_months"), limit: int = Query(8, ge=1, le=20)
) -> dict[str, Any]:
    return {"period": analytics.period(period).to_dict(), "states": analytics.top_states(period, limit)}


@app.get("/api/analysis/revenue-decline")
def revenue_decline(month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$")) -> dict[str, Any]:
    return analytics.revenue_decline_analysis(month)


@app.get("/api/quality")
def quality() -> dict[str, Any]:
    report = db.quality_report()
    return {
        "generated_at": report.get("generated_at"),
        "as_of": report.get("as_of"),
        "schema_version": report.get("schema_version"),
        "raw_counts": report.get("raw_counts", {}),
        "clean_counts": report.get("clean_counts", {}),
        "issues": report.get("issues", []),
        "total_issues": report.get("total_issues", 0),
        "checks": report.get("checks", []),
        "revenue_definition": report.get("revenue_definition"),
        "currency_definition": report.get("currency_definition"),
    }


# ---------------------------------------------------------------------------
# AI analyst
# ---------------------------------------------------------------------------
@app.post("/api/ask")
def ask(payload: AskRequest, request: Request) -> dict[str, Any]:
    client = request.client.host if request.client else "anonymous"
    limiter.check(client)
    result = agent.ask(payload.question)
    return result.to_dict()


@app.get("/api/examples")
def examples() -> dict[str, Any]:
    return {"questions": suggested_questions(analytics)}


# ---------------------------------------------------------------------------
# Static dashboard
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg")


@app.get("/{path:path}")
def spa_fallback(path: str):
    if path.startswith("api/"):
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": "Endpoint not found.", "details": {}}},
        )
    candidate = STATIC_DIR / path
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")


def run() -> None:
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    run()
