"""API contract tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_and_meta():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["counts"]["customers"] == 2200

    meta = client.get("/api/meta").json()
    assert meta["business"]["currency"] == "INR"
    assert meta["business"]["as_of"] == "2025-09-30"
    assert meta["business"]["currency_symbol"] == "₹"
    assert len(meta["periods"]) == 7
    assert len(meta["suggested_questions"]) >= 8
    assert "api_key" not in str(meta).lower()


def test_overview_contract():
    response = client.get("/api/overview", params={"period": "latest_month"})
    assert response.status_code == 200
    body = response.json()
    assert body["period"]["key"] == "latest_month"
    assert set(body["kpis"]["kpis"]) >= {"revenue", "orders", "customers", "aov"}
    assert body["kpis"]["kpis"]["revenue"]["value"] > 0
    assert body["insights"]
    assert body["payment_mix"]
    assert body["city_tier_mix"]
    assert body["channel_performance"]
    assert body["top_states"]
    assert body["llm"]["mode"] in {"offline", "llm"}
    assert "₹" in body["headline"]


def test_chart_endpoints():
    specs = [
        ("/api/charts/monthly-revenue", {}),
        ("/api/charts/revenue-by-category", {}),
        ("/api/charts/top-products", {}),
        ("/api/charts/customer-trend", {}),
        ("/api/charts/payment-mix", {}),
        ("/api/charts/city-tier", {}),
        ("/api/charts/channel", {}),
    ]
    for path, params in specs:
        response = client.get(path, params=params)
        assert response.status_code == 200, path
        body = response.json()
        assert body["labels"] and body["datasets"], path
        assert body["datasets"][0]["data"], path


def test_analytics_endpoints():
    assert client.get("/api/kpis", params={"period": "all_time"}).status_code == 200
    assert client.get("/api/customers/metrics").json()["total_customers"] == 2200
    assert client.get("/api/customers/inactive", params={"limit": 3}).json()["returned"] == 3
    assert client.get("/api/products/top", params={"limit": 3}).json()["products"][0]["revenue"] > 0
    assert client.get("/api/products/low", params={"limit": 3}).json()["products"]
    assert client.get("/api/payments").json()["payment_mix"][0]["payment_method"] == "UPI"
    assert client.get("/api/city-tiers").json()["city_tiers"]
    assert client.get("/api/channels").json()["channels"]
    assert client.get("/api/states", params={"limit": 3}).json()["states"][0]["state"] == "Maharashtra"


def test_decline_analysis_endpoint():
    body = client.get("/api/analysis/revenue-decline").json()
    assert body["month"] == "2025-09"
    assert body["previous_month"] == "2025-08"
    assert body["change"]["revenue_delta"] < 0
    assert body["context_notes"]


def test_quality_endpoint():
    body = client.get("/api/quality").json()
    assert body["checks"]
    assert all(check["status"] == "pass" for check in body["checks"])
    assert body["total_issues"] > 0
    assert "INR" in body["currency_definition"]


def test_ask_endpoint_offline():
    response = client.post("/api/ask", json={"question": "Why did revenue decline last month?"})
    assert response.status_code == 200
    body = response.json()
    assert body["supported"]
    assert body["grounding"]["status"] == "verified"
    assert body["tool_trace"]
    assert "₹" in body["answer"]


def test_ask_endpoint_chart_and_unsupported():
    chart = client.post("/api/ask", json={"question": "Generate a pie chart of payment mix"}).json()
    assert chart["chart"] is not None
    assert chart["chart"]["type"] == "doughnut"

    unsupported = client.post("/api/ask", json={"question": "Forecast next quarter revenue."}).json()
    assert not unsupported["supported"]
    assert "can't answer" in unsupported["answer"].lower()


def test_validation_errors():
    bad_period = client.get("/api/overview", params={"period": "last_week"})
    assert bad_period.status_code in (400, 422)
    assert bad_period.json()["error"]["code"] == "invalid_period"

    short_question = client.post("/api/ask", json={"question": "hi"})
    assert short_question.status_code == 422
    assert short_question.json()["error"]["code"] == "validation_error"

    bad_month = client.get("/api/analysis/revenue-decline", params={"month": "not-a-month"})
    assert bad_month.status_code == 422


def test_static_pages():
    assert client.get("/").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/chart.umd.min.js").status_code == 200
    assert client.get("/favicon.svg").status_code == 200
