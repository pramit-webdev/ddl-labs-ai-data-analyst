"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    # provider: (base_url, default_model)
    "groq": ("https://api.groq.com/openai/v1", "qwen/qwen3.8-27b"),
    "nvidia": (
        "https://integrate.api.nvidia.com/v1",
        "meta/llama-3.3-70b-instruct",
    ),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "meta-llama/llama-3.3-70b-instruct:free",
    ),
    "custom": ("", ""),
    "offline": ("", ""),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "Atlas BI"
    app_version: str = "1.0.0"
    environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))

    root_dir: Path = ROOT_DIR
    data_dir: Path = ROOT_DIR / "data"
    raw_dir: Path = ROOT_DIR / "data" / "raw"
    db_path: Path = ROOT_DIR / "data" / "warehouse.db"
    quality_report_path: Path = ROOT_DIR / "data" / "quality_report.json"
    expected_metrics_path: Path = ROOT_DIR / "data" / "expected_metrics.json"
    static_dir: Path = ROOT_DIR / "app" / "static"

    llm_provider: str = field(
        default_factory=lambda: os.getenv("LLM_PROVIDER", "offline").strip().lower()
    )
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", "").strip())
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "").strip())
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "").strip())
    llm_timeout_seconds: float = field(
        default_factory=lambda: _env_float("LLM_TIMEOUT_SECONDS", 45.0)
    )
    llm_max_steps: int = field(default_factory=lambda: _env_int("LLM_MAX_STEPS", 6))

    ask_rate_limit_per_minute: int = field(
        default_factory=lambda: _env_int("ASK_RATE_LIMIT_PER_MINUTE", 20)
    )
    insights_cache_ttl_seconds: int = field(
        default_factory=lambda: _env_int("INSIGHTS_CACHE_TTL_SECONDS", 600)
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())

    @property
    def resolved_base_url(self) -> str:
        if self.llm_base_url:
            return self.llm_base_url.rstrip("/")
        return PROVIDER_DEFAULTS.get(self.llm_provider, ("", ""))[0]

    @property
    def resolved_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return PROVIDER_DEFAULTS.get(self.llm_provider, ("", ""))[1]

    @property
    def llm_configured(self) -> bool:
        return bool(
            self.llm_provider not in {"offline", ""}
            and self.llm_api_key
            and self.resolved_base_url
            and self.resolved_model
        )

    @property
    def llm_mode(self) -> str:
        return "llm" if self.llm_configured else "offline"


settings = Settings()
