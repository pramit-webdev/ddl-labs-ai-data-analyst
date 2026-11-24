# Submission pack — DDL Labs Technical Assignment

**Candidate:** Pramit Das

| Field | Value |
|---|---|
| Candidate Name | Pramit Das |
| GitHub Repository | https://github.com/pramit-webdev/ddl-labs-ai-data-analyst |
| Demo Video | https://github.com/pramit-webdev/ddl-labs-ai-data-analyst/releases/download/demo-v1/demo.mp4 |
| LLM Used | Groq `llama-3.3-70b-versatile` via OpenAI-compatible tool calling. Automatic verified deterministic fallback when no key is configured. |
| Database Used | SQLite — `data/warehouse.db`, built from committed raw CSVs by `app/ingest.py` |
| Main Technologies | Python 3.12, FastAPI, SQLite, pandas/numpy, OpenAI-compatible tool calling, Playwright, vanilla HTML/CSS/JS, Chart.js |
| Known Limitations | Synthetic dataset; no profit/inventory/forecast data; revenue excludes shipping and taxes; external benchmarks unsupported; LLM needs a provider key for live mode. |

## Verification summary

- `pytest -q` — 49 tests passing (unit, integration, API contract and Playwright E2E).
- Independent oracle `data/expected_metrics.json` matches the SQL path to within cent-level rounding.
- Browser-verified: overview, period switching, grounded chat, chart generation, quality page, mobile layout.
- Demo video recorded from the running application with voiceover.

## Run locally

```bash
python scripts/generate_sample_data.py
python -m app.ingest
python -m uvicorn app.main:app
# open http://127.0.0.1:8000
```

See `README.md` for full documentation.
