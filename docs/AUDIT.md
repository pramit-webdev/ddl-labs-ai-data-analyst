# Assignment audit — DDL Labs Technical Assignment

Audit against `DDL Labs Technical Assignment 1.pdf` (read 2026-11-25).

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Working AI Data Analyst & BI Agent | Complete | FastAPI app, grounded chat, dashboard |
| 2 | E-commerce/retail customers/orders/products/sales | Complete | `scripts/generate_sample_data.py`, Indian retail dataset |
| 3 | Data ingestion: clean, validate, missing values, duplicates, types | Complete | `app/ingest.py`, 19 quality checks, raw→clean counts |
| 4 | Sales: revenue, orders, AOV, monthly revenue, category/product revenue | Complete | `app/analytics.py` |
| 5 | Customers: total, new, repeat, inactive, top | Complete | `customer_metrics`, `customer_trend`, `inactive_customers`, `top_customers` |
| 6 | Products: top-selling, highest-revenue, low-performing | Complete | `top_products`, `low_performing_products` |
| 7 | Natural-language questions | Complete | `/api/ask`, deterministic + LLM paths |
| 8 | LLM integration with stated provider/model | Complete | Groq `qwen/qwen3.8-27b` in `app/agent/llm.py` |
| 9 | Controlled tool/function layer | Complete | `app/agent/tools.py` (16 tools) |
| 10 | Grounded answers, no invented numbers | Complete | `app/agent/grounding.py`, fallback |
| 11 | Unsupported/invalid input and DB/LLM failures | Complete | `app/errors.py`, query guard, fallback |
| 12 | Dashboard KPIs: revenue/orders/customers/AOV | Complete | `/api/overview`, `index.html` |
| 13 | Charts: monthly revenue, category, top products, customer trend | Complete | Chart specs + Chart.js |
| 14 | AI-generated data-backed insights | Complete | `deterministic_insights`, LLM answer |
| 15 | Multi-step “why revenue declined last month” | Complete | `revenue_decline_analysis` |
| 16 | Dynamic chart generation | Complete | `generate_chart`, chat rendering |
| 17 | Backend/API validation and error handling | Complete | FastAPI endpoints, `AppError` |
| 18 | Tests for processing, calculations, API, AI, errors | Complete | 51 tests |
| 19 | Public GitHub repo, runnable locally | Complete | repo public, README, `requirements.txt` |
| 20 | README: purpose, setup, tech, LLM, DB, features, approach, tests, limits | Complete | `README.md` |
| 21 | Demo video | Complete | release asset `demo.mp4` |
| 22 | Submission details | Complete | `docs/SUBMISSION.md` |
| 23 | Known limitations | Complete | README/SUBMISSION |

## Extra assignment-relevant capabilities

- India-specific analytics: payment mix, city tiers, state performance, channels.
- Festive-calendar context for the September decline.
- Read-only guarded SQL tool with authorizer and timeout.
- Free deployment on Render and Docker.
- Deterministic offline fallback when no LLM key is configured.

## Verification

- `pytest -q`: 51 passed.
- Fresh warehouse rebuild: 2,200 customers, 160 products, 12,497 completed orders, 23,489 completed items.
- All 6 database integrity checks pass.
- Live LLM path verified against Groq: `mode=llm`, `grounding=verified`.
- Browser E2E verified overview, period switching, grounded chat, chart, quality page, unsupported question and mobile layout.
- Docker image builds and container serves `/api/health`.

## UI/UX and demo improvements

- Redesigned the dashboard with a modern card system, KPI icons, gradients, responsive layout and improved chat styling.
- Replaced the demo with a narrated 63-second walkthrough showing the live Groq tool-calling path, tool trace, grounding badge and data-quality page.
- Updated screenshots to reflect the redesigned UI.

## Known limitations

- Synthetic dataset, not real company data.
- No profit, inventory, forecast or external benchmark data.
- Free-tier LLM quota can trigger fallback to the deterministic analyst.
- Render free service sleeps after inactivity.
