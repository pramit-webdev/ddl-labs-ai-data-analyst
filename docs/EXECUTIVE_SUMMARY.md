# Solution Executive Summary

Atlas BI is a full-stack AI Data Analyst and Business Intelligence Agent built for an Indian e-commerce retailer. It lets a business user ask natural-language questions about revenue, orders, customers, products, payments, city tiers and seasonal demand, while ensuring every answer is grounded in the actual warehouse data.

The backend is FastAPI with a SQLite warehouse generated from intentionally dirty CSV exports. A deterministic cleaning pipeline normalizes currencies, dates, statuses, channels, payment methods and city tiers; removes duplicates and orphans; validates referential integrity; and writes a machine-readable data-quality report. The cleaned warehouse contains 2,200 customers, 160 products, 12,497 completed orders and 23,489 completed order items. All analytics are anchored to the latest data month, September 2025, so the demo remains stable over time.

Analytics are centralized in one service and exposed through REST endpoints. The dashboard shows revenue, orders, customers and average order value, plus monthly revenue, category and payment mix, city-tier and state performance, customer trends, top products and top states. Charts are generated dynamically from real data.

The AI layer uses Groq with `qwen/qwen3.8-27b` through an OpenAI-compatible tool-calling loop. The model never accesses the database directly. It calls controlled tools such as `get_sales_metrics`, `get_top_products`, `get_payment_mix`, `get_city_tier_mix`, `get_revenue_decline_analysis` and `generate_chart`. A read-only SQL tool is available but restricted by an allow-list, single-statement validation and a two-second timeout. Every answer passes a numeric grounding check that compares currency, percentage and count claims against tool results. If the provider fails, returns an ungrounded answer, or exceeds the step budget, Atlas BI falls back to a deterministic analyst and explains why.

The required multi-step analysis, “Why did revenue decline last month?”, decomposes the change into volume and average-order-value effects, then examines category, product, channel, segment, payment, city-tier, state and customer activity. For September 2025, revenue fell 16.4% even though orders rose 2.3%; the decline was driven by an 18.4% AOV drop, with Electronics accounting for approximately 112% of the change. Festive-calendar context is included because September is the pre-Diwali Shradh lull.

The frontend is a responsive dashboard with grounded chat and dynamic charts; the app is deployed on Render and ships as Docker.

Testing includes 51 tests: ingestion against an independently computed oracle, analytics calculations, period validation, decline decomposition, SQL safety, tool validation, grounding, planner classification, scripted LLM tool-loop and fallback tests, API contracts and Playwright browser E2E tests. The demo video is a 93-second cinematic walkthrough with realistic Indian-English voiceover.

Known limitations: the dataset is synthetic; revenue excludes shipping and taxes; profit, inventory and forecasting are out of scope; LLM answers depend on provider quota and otherwise use the verified deterministic fallback.

Candidate: Pramit Das
GitHub Repository: https://github.com/pramit-webdev/ddl-labs-ai-data-analyst
Demo Video: https://github.com/pramit-webdev/ddl-labs-ai-data-analyst/releases/download/demo-v1/demo.mp4
LLM Used: Groq qwen/qwen3.8-27b with deterministic fallback
Database Used: SQLite (data/warehouse.db, rebuilt from committed CSVs)
Main Technologies: Python 3.12, FastAPI, SQLite, pandas/numpy, OpenAI-compatible tool calling, Playwright, vanilla HTML/CSS/JS, Chart.js
Known Limitations: Synthetic dataset; no profit/inventory/forecast data; revenue excludes shipping and taxes; external benchmarks unsupported; LLM free-tier quota may trigger verified fallback.