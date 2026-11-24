"""Shared fixtures for the test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.analytics import AnalyticsService
from app.config import settings
from app.db import db


@pytest.fixture(scope="session", autouse=True)
def warehouse():
    db.initialize()
    assert db.exists
    return db


@pytest.fixture(scope="session")
def expected() -> dict:
    return json.loads(settings.expected_metrics_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def quality() -> dict:
    return json.loads(settings.quality_report_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def analytics() -> AnalyticsService:
    return AnalyticsService(db)
