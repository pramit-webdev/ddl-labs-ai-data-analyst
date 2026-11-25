"""Browser E2E tests (require the app running on BASE_URL)."""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")
pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    errors = []
    page = context.new_page()
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector("#kpi-grid .kpi-value", timeout=15000)
    yield page, errors
    context.close()


def test_overview_renders_real_metrics(page):
    browser_page, errors = page
    assert browser_page.locator("h1#overview-title").inner_text() == "Namaste. Here is the business."
    values = browser_page.locator("#kpi-grid .kpi-value").all_inner_texts()
    assert len(values) == 4
    assert any("₹" in value for value in values)
    assert browser_page.locator("#chart-monthly").is_visible()
    assert browser_page.locator("#insight-grid .insight-card").count() >= 5
    assert browser_page.locator("#top-products-table tbody tr").count() >= 5
    assert browser_page.locator("#top-states-table tbody tr").count() >= 5
    assert "2025-09-30" in browser_page.locator("#hero-badge").inner_text()
    assert not errors, errors


def test_period_selector_updates_overview(page):
    browser_page, errors = page
    browser_page.select_option("#period-select", "latest_month")
    browser_page.wait_for_function(
        "() => document.querySelector('#insight-text').textContent.includes('Latest month revenue')"
    )
    assert "54,43,685" in browser_page.locator("#insight-text").inner_text()
    assert not errors, errors


def test_chat_returns_grounded_decline_analysis(page):
    browser_page, errors = page
    browser_page.click("#ask-shortcut")
    browser_page.wait_for_selector("#page-ask.active")
    browser_page.fill("#ask-input", "Why did revenue decline last month?")
    browser_page.click("#ask-submit")
    browser_page.wait_for_function(
        "() => document.querySelectorAll('#chat .message.assistant .message-content')[1]?.innerText.includes('Electronics')",
        timeout=20000,
    )
    answer = browser_page.locator("#chat .message.assistant .message-content").last.inner_text()
    assert "Electronics" in answer
    assert "₹" in answer
    assert browser_page.locator("#chat details.trace").count() >= 1
    assert browser_page.locator("#chat .chip-up").count() >= 1
    assert not errors, errors


def test_chat_generates_chart(page):
    browser_page, errors = page
    browser_page.click("#ask-shortcut")
    browser_page.wait_for_selector("#page-ask.active")
    browser_page.fill("#ask-input", "Generate a chart of revenue by category")
    browser_page.click("#ask-submit")
    browser_page.wait_for_selector("#chat .message-chart canvas", timeout=20000)
    assert browser_page.locator("#chat .message-chart canvas").is_visible()
    assert not errors, errors


def test_quality_page(page):
    browser_page, errors = page
    browser_page.evaluate("location.hash = '#quality'")
    browser_page.wait_for_selector("#quality-kpis .kpi-value")
    values = browser_page.locator("#quality-kpis .kpi-value").all_inner_texts()
    assert len(values) == 4
    assert browser_page.locator("#checks-list .check-item.pass").count() >= 6
    assert browser_page.locator("#issues-table tbody tr").count() >= 10
    assert not errors, errors


def test_unsupported_question(page):
    browser_page, errors = page
    browser_page.click("#ask-shortcut")
    browser_page.wait_for_selector("#page-ask.active")
    browser_page.fill("#ask-input", "Forecast next quarter revenue")
    browser_page.click("#ask-submit")
    browser_page.wait_for_function(
        "() => { const nodes = document.querySelectorAll('#chat .message.assistant .message-content'); const last = nodes[nodes.length - 1]; return last && /can'?t|cannot|unsupported|don't have/i.test(last.innerText); }",
        timeout=20000,
    )
    answer = browser_page.locator("#chat .message.assistant .message-content").last.inner_text().lower()
    assert "forecast" in answer or "unsupported" in answer or "can't" in answer
    assert not errors, errors


def test_mobile_layout_has_no_horizontal_overflow(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(BASE_URL, wait_until="networkidle")
    page.wait_for_selector("#kpi-grid .kpi-value", timeout=15000)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 2, f"horizontal overflow: {overflow}px"
    assert page.locator("#menu-toggle").is_visible()
    page.click("#menu-toggle")
    assert page.locator("#sidebar.open").count() == 1
    assert not errors, errors
    context.close()
