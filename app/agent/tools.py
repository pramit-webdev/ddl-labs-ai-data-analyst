"""Controlled tool layer exposed to the LLM and the deterministic planner."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.analytics import AnalyticsService, money, num, pct, signed_money, signed_pct
from app.agent.query_guard import SafeQueryError, run_safe_query

PeriodKey = Literal[
    "latest_month", "previous_month", "last_30_days", "last_90_days",
    "last_12_months", "ytd", "all_time",
]
ChartMetric = Literal[
    "monthly_revenue", "revenue_by_category", "top_products", "customer_trend",
    "payment_mix", "city_tier", "channel",
]


class ToolError(Exception):
    pass


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PeriodArgs(_Args):
    period: PeriodKey = "last_12_months"


class MonthlyArgs(_Args):
    months: int = Field(12, ge=3, le=24)


class TopProductsArgs(_Args):
    metric: Literal["revenue", "units"] = "revenue"
    limit: int = Field(5, ge=1, le=20)
    period: PeriodKey = "last_12_months"


class LowProductsArgs(_Args):
    limit: int = Field(5, ge=1, le=20)
    period: PeriodKey = "last_12_months"


class CustomerMetricsArgs(_Args):
    inactive_days: int = Field(90, ge=1, le=730)


class InactiveCustomersArgs(_Args):
    inactive_days: int = Field(90, ge=1, le=730)
    limit: int = Field(10, ge=1, le=50)


class TopCustomersArgs(_Args):
    limit: int = Field(5, ge=1, le=20)
    period: PeriodKey = "last_12_months"


class TrendArgs(_Args):
    months: int = Field(12, ge=3, le=24)


class StatesArgs(_Args):
    period: PeriodKey = "last_12_months"
    limit: int = Field(8, ge=1, le=20)


class DeclineArgs(_Args):
    month: str | None = Field(None, pattern=r"^\d{4}-\d{2}$")


class ChartArgs(_Args):
    chart_type: Literal["line", "bar", "doughnut"] = "line"
    metric: ChartMetric = "monthly_revenue"
    period: PeriodKey = "last_12_months"
    months: int = Field(12, ge=3, le=24)
    limit: int = Field(8, ge=1, le=20)


class QueryArgs(_Args):
    sql: str = Field(..., min_length=1, max_length=2000)


ARG_MODELS: dict[str, type[_Args]] = {
    "get_sales_metrics": PeriodArgs,
    "get_monthly_revenue": MonthlyArgs,
    "get_revenue_by_category": PeriodArgs,
    "get_top_products": TopProductsArgs,
    "get_low_performing_products": LowProductsArgs,
    "get_customer_metrics": CustomerMetricsArgs,
    "get_inactive_customers": InactiveCustomersArgs,
    "get_top_customers": TopCustomersArgs,
    "get_customer_trend": TrendArgs,
    "get_payment_mix": PeriodArgs,
    "get_city_tier_mix": PeriodArgs,
    "get_channel_performance": PeriodArgs,
    "get_top_states": StatesArgs,
    "get_revenue_decline_analysis": DeclineArgs,
    "generate_chart": ChartArgs,
    "query_data": QueryArgs,
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_PERIOD = {"type": "string", "enum": list(PeriodKey.__args__)}
_INT = {"type": "integer", "minimum": 1, "maximum": 20}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": _schema(properties, required),
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool("get_sales_metrics", "Revenue, orders, purchasing customers and AOV for a period.", {"period": _PERIOD}),
    _tool("get_monthly_revenue", "Monthly revenue, orders and AOV for the last N months.", {
        "months": {"type": "integer", "minimum": 3, "maximum": 24, "default": 12}}),
    _tool("get_revenue_by_category", "Category revenue, units and share for a period.", {"period": _PERIOD}),
    _tool("get_top_products", "Top products by revenue or units.", {
        "metric": {"type": "string", "enum": ["revenue", "units"], "default": "revenue"},
        "limit": _INT, "period": _PERIOD}),
    _tool("get_low_performing_products", "Lowest-revenue active products.", {"limit": _INT, "period": _PERIOD}),
    _tool("get_customer_metrics", "Customer counts, repeat rate and inactive customers.", {
        "inactive_days": {"type": "integer", "minimum": 1, "maximum": 730, "default": 90}}),
    _tool("get_inactive_customers", "Lapsed customers by lifetime value.", {
        "inactive_days": {"type": "integer", "minimum": 1, "maximum": 730, "default": 90},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}),
    _tool("get_top_customers", "Top customers by revenue.", {"limit": _INT, "period": _PERIOD}),
    _tool("get_customer_trend", "Monthly new vs returning customers.", {
        "months": {"type": "integer", "minimum": 3, "maximum": 24, "default": 12}}),
    _tool("get_payment_mix", "Revenue share by Indian payment method.", {"period": _PERIOD}),
    _tool("get_city_tier_mix", "Revenue by Tier 1/2/3 and international cities.", {"period": _PERIOD}),
    _tool("get_channel_performance", "Revenue and AOV by sales channel.", {"period": _PERIOD}),
    _tool("get_top_states", "Top Indian states by revenue.", {"period": _PERIOD, "limit": _INT}),
    _tool("get_revenue_decline_analysis", "Multi-step root-cause analysis: orders vs AOV, category/product/channel/payment/tier/state drivers and calendar context.", {
        "month": {"type": "string", "pattern": "^\\d{4}-\\d{2}$"}}),
    _tool("generate_chart", "Build a chart spec from real data.", {
        "chart_type": {"type": "string", "enum": ["line", "bar", "doughnut"], "default": "line"},
        "metric": {"type": "string", "enum": list(ChartMetric.__args__), "default": "monthly_revenue"},
        "period": _PERIOD,
        "months": {"type": "integer", "minimum": 3, "maximum": 24, "default": 12},
        "limit": _INT}),
    _tool("query_data", "Read-only SELECT/WITH over allowed tables; LIMIT enforced.", {
        "sql": {"type": "string", "description": "A single SQLite SELECT statement."}}, ["sql"]),
]


def _compact_value(value: Any, list_limit: int = 4) -> Any:
    """Shrink verbose tool payloads while keeping every scalar fact."""
    if isinstance(value, dict):
        return {key: _compact_value(item, list_limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        kept = [_compact_value(item, list_limit) for item in value[:list_limit]]
        if len(value) > list_limit:
            kept.append(f"... {len(value) - list_limit} more rows (see summary)")
        return kept
    if isinstance(value, str) and len(value) > 260:
        return value[:260] + "..."
    return value


def compact_for_llm(data: Any, limit: int = 4800) -> str:
    """Serialize a tool result for the model without exceeding provider TPM."""
    for list_limit in (4, 3, 2, 1):
        payload = json.dumps(_compact_value(data, list_limit), default=str)
        if len(payload) <= limit:
            return payload
    return json.dumps(_compact_value(data, 1), default=str)[:limit] + "... [truncated]"


@dataclass
class ToolOutcome:
    name: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    data: Any = None
    chart: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0

    def to_trace(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "summary": self.summary,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "has_chart": self.chart is not None,
        }

    def to_llm_payload(self) -> str:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
        }
        if self.chart:
            # The chart spec is already part of data for generate_chart.
            if self.name != "generate_chart":
                payload["chart"] = self.chart
        if self.error:
            payload["error"] = self.error
        return compact_for_llm(payload)


class ToolRegistry:
    def __init__(self, analytics: AnalyticsService):
        self.analytics = analytics
        self._dispatch: dict[str, Callable[[Any], tuple[Any, str, dict | None]]] = {
            "get_sales_metrics": self._sales_metrics,
            "get_monthly_revenue": self._monthly_revenue,
            "get_revenue_by_category": self._revenue_by_category,
            "get_top_products": self._top_products,
            "get_low_performing_products": self._low_products,
            "get_customer_metrics": self._customer_metrics,
            "get_inactive_customers": self._inactive_customers,
            "get_top_customers": self._top_customers,
            "get_customer_trend": self._customer_trend,
            "get_payment_mix": self._payment_mix,
            "get_city_tier_mix": self._city_tier_mix,
            "get_channel_performance": self._channel_performance,
            "get_top_states": self._top_states,
            "get_revenue_decline_analysis": self._decline,
            "generate_chart": self._generate_chart,
            "query_data": self._query_data,
        }

    @staticmethod
    def schemas() -> list[dict[str, Any]]:
        return TOOL_SCHEMAS

    def execute(self, name: str, arguments: dict[str, Any] | None) -> ToolOutcome:
        started = time.perf_counter()
        arguments = arguments or {}
        if name not in ARG_MODELS:
            return ToolOutcome(
                name=name, arguments=arguments, ok=False,
                summary="Unknown tool.",
                error=f"Unknown tool '{name}'. Available tools: {', '.join(ARG_MODELS)}.",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        try:
            parsed = ARG_MODELS[name](**arguments)
        except ValidationError as exc:
            errors = "; ".join(
                f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
            )
            return ToolOutcome(
                name=name, arguments=arguments, ok=False,
                summary="Invalid tool arguments.",
                error=errors,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        try:
            data, summary, chart = self._dispatch[name](parsed)
            return ToolOutcome(
                name=name,
                arguments=parsed.model_dump(),
                ok=True,
                summary=summary,
                data=data,
                chart=chart,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except (ToolError, SafeQueryError, ValueError) as exc:
            return ToolOutcome(
                name=name,
                arguments=parsed.model_dump(),
                ok=False,
                summary="Tool failed.",
                error=str(exc),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return ToolOutcome(
                name=name,
                arguments=parsed.model_dump(),
                ok=False,
                summary="Tool failed.",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    # ------------------------------------------------------------------
    # Implementations
    # ------------------------------------------------------------------
    def _sales_metrics(self, args: PeriodArgs):
        result = self.analytics.kpis(args.period)
        k = result["kpis"]
        rev = k["revenue"]
        summary = (
            f"{result['period']['label']}: revenue {money(rev['value'])}, "
            f"{num(k['orders']['value'])} orders, {num(k['customers']['value'])} purchasing customers, "
            f"AOV {money(k['aov']['value'])}."
        )
        if rev["delta_pct"] is not None:
            summary += (
                f" Revenue is {signed_pct(rev['delta_pct'])} vs the previous period "
                f"({money(rev['previous'])})."
            )
        return result, summary, None

    def _monthly_revenue(self, args: MonthlyArgs):
        rows = self.analytics.monthly_revenue(args.months)
        latest, previous = rows[-1], rows[-2] if len(rows) > 1 else None
        summary = (
            f"Monthly revenue for the last {len(rows)} months. Latest month {latest['full_label']}: "
            f"{money(latest['revenue'])} from {num(latest['orders'])} orders (AOV {money(latest['aov'])})."
        )
        if previous and previous["revenue"]:
            change = (latest["revenue"] - previous["revenue"]) / previous["revenue"] * 100
            summary += f" That is {signed_pct(change)} vs {previous['full_label']} ({money(previous['revenue'])})."
        return rows, summary, None

    def _revenue_by_category(self, args: PeriodArgs):
        rows = self.analytics.revenue_by_category(args.period)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No completed revenue in {p.label}.", None
        top = rows[0]
        summary = (
            f"{p.label}: {top['category']} leads with {money(top['revenue'])} "
            f"({top['share_pct']:.1f}% of revenue) from {num(top['orders'])} orders. "
            f"{len(rows)} categories have sales."
        )
        return rows, summary, None

    def _top_products(self, args: TopProductsArgs):
        rows = self.analytics.top_products(args.period, args.metric, args.limit)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No product sales in {p.label}.", None
        top = rows[0]
        summary = (
            f"{p.label}: {top['name']} is #1 by {args.metric} with "
            f"{money(top['revenue']) if args.metric == 'revenue' else num(top['units']) + ' units'} "
            f"from {num(top['orders'])} orders."
        )
        if len(rows) > 1:
            second = rows[1]
            summary += (
                f" #2 is {second['name']} with "
                f"{money(second['revenue']) if args.metric == 'revenue' else num(second['units']) + ' units'}."
            )
        return rows, summary, None

    def _low_products(self, args: LowProductsArgs):
        rows = self.analytics.low_performing_products(args.period, args.limit)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No active products in {p.label}.", None
        worst = rows[0]
        summary = (
            f"{p.label}: {worst['name']} is the lowest-revenue active product with "
            f"{money(worst['revenue'])} from {num(worst['units'])} units."
        )
        return rows, summary, None

    def _customer_metrics(self, args: CustomerMetricsArgs):
        data = self.analytics.customer_metrics(args.inactive_days)
        summary = (
            f"{num(data['purchasing_customers'])} customers have purchased "
            f"(out of {num(data['total_customers'])} total). Repeat rate is {pct(data['repeat_rate_pct'])}. "
            f"{num(data['inactive_customers'])} customers ({pct(data['inactive_rate_pct'])}) have not "
            f"purchased in {data['inactive_days']}+ days; {num(data['never_purchased'])} have never purchased."
        )
        return data, summary, None

    def _inactive_customers(self, args: InactiveCustomersArgs):
        data = self.analytics.inactive_customers(args.inactive_days, args.limit)
        if not data["customers"]:
            summary = f"No customers have been inactive for {data['inactive_days']}+ days."
        else:
            top = data["customers"][0]
            summary = (
                f"{num(data['total_inactive'])} customers have not purchased in {data['inactive_days']}+ days. "
                f"Highest lifetime value among them: {top['name']} ({money(top['lifetime_revenue'])}, "
                f"last order {top['last_order']}, {top['days_since_last_order']} days ago)."
            )
        return data, summary, None

    def _top_customers(self, args: TopCustomersArgs):
        rows = self.analytics.top_customers(args.period, args.limit)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No customer revenue in {p.label}.", None
        top = rows[0]
        summary = (
            f"{p.label}: {top['name']} is the top customer with {money(top['revenue'])} "
            f"from {num(top['orders'])} orders."
        )
        return rows, summary, None

    def _customer_trend(self, args: TrendArgs):
        rows = self.analytics.customer_trend(args.months)
        latest = rows[-1]
        summary = (
            f"Customer trend for the last {len(rows)} months. In {latest['label']}: "
            f"{num(latest['new_customers'])} new and {num(latest['returning_customers'])} returning customers, "
            f"with {money(latest['new_revenue'])} from new and {money(latest['returning_revenue'])} from returning customers."
        )
        return rows, summary, None

    def _payment_mix(self, args: PeriodArgs):
        rows = self.analytics.payment_mix(args.period)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No payment data in {p.label}.", None
        top = rows[0]
        upi = next((r for r in rows if r["payment_method"] == "UPI"), None)
        summary = (
            f"{p.label}: {top['payment_method']} leads with {money(top['revenue'])} "
            f"({top['share_pct']:.1f}% of revenue) from {num(top['orders'])} orders."
        )
        if upi and upi["payment_method"] != top["payment_method"]:
            summary += f" UPI accounts for {upi['share_pct']:.1f}%."
        return rows, summary, None

    def _city_tier_mix(self, args: PeriodArgs):
        rows = self.analytics.city_tier_mix(args.period)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No city-tier data in {p.label}.", None
        top = rows[0]
        tier23 = sum(r["share_pct"] for r in rows if r["city_tier"] in {"Tier 2", "Tier 3"})
        summary = (
            f"{p.label}: {top['city_tier']} cities contribute {top['share_pct']:.1f}% of revenue; "
            f"Tier 2 + Tier 3 together contribute {tier23:.1f}%."
        )
        return rows, summary, None

    def _channel_performance(self, args: PeriodArgs):
        rows = self.analytics.channel_performance(args.period)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No channel data in {p.label}.", None
        top = rows[0]
        summary = (
            f"{p.label}: {top['channel']} leads with {money(top['revenue'])} "
            f"({top['share_pct']:.1f}% of revenue) and an AOV of {money(top['aov'])}."
        )
        return rows, summary, None

    def _top_states(self, args: StatesArgs):
        rows = self.analytics.top_states(args.period, args.limit)
        p = self.analytics.period(args.period)
        if not rows:
            return rows, f"No state-level data in {p.label}.", None
        top = rows[0]
        summary = (
            f"{p.label}: {top['state']} is the top state with {money(top['revenue'])} "
            f"from {num(top['orders'])} orders."
        )
        return rows, summary, None

    def _decline(self, args: DeclineArgs):
        data = self.analytics.revenue_decline_analysis(args.month)
        summary = " ".join(data["summary_lines"][:4])
        if data.get("context_notes"):
            summary += " " + data["context_notes"][0]
        return data, summary, None

    def _generate_chart(self, args: ChartArgs):
        if args.metric == "monthly_revenue":
            chart = self.analytics.monthly_revenue_chart(args.months)
            latest = chart["datasets"][0]["data"][-1] if chart["datasets"][0]["data"] else 0
            summary = (
                f"Generated a {args.chart_type} chart of monthly revenue for the last {args.months} months; "
                f"latest value {money(latest)}."
            )
        elif args.metric == "revenue_by_category":
            chart = self.analytics.revenue_by_category_chart(args.period)
            top = chart["labels"][0] if chart["labels"] else "n/a"
            summary = f"Generated a {args.chart_type} chart of revenue by category; {top} leads."
        elif args.metric == "top_products":
            chart = self.analytics.top_products_chart(args.period, args.limit)
            summary = f"Generated a {args.chart_type} chart of the top {args.limit} products by revenue."
        elif args.metric == "payment_mix":
            chart = self.analytics.payment_mix_chart(args.period)
            summary = f"Generated a {args.chart_type} chart of revenue by payment method."
        elif args.metric == "city_tier":
            chart = self.analytics.city_tier_chart(args.period)
            summary = f"Generated a {args.chart_type} chart of revenue by city tier."
        elif args.metric == "channel":
            chart = self.analytics.channel_chart(args.period)
            summary = f"Generated a {args.chart_type} chart of revenue by channel."
        else:
            chart = self.analytics.customer_trend_chart(args.months)
            summary = f"Generated a {args.chart_type} chart of new vs returning customers for the last {args.months} months."
        if args.chart_type != chart["type"]:
            chart["type"] = args.chart_type
        if args.metric == "top_products" and args.chart_type == "bar":
            chart.setdefault("meta", {})["horizontal"] = True
        return chart, summary, chart

    def _query_data(self, args: QueryArgs):
        data = run_safe_query(args.sql, self.analytics.db)
        columns = ", ".join(data["columns"]) or "no columns"
        summary = f"Read-only query returned {data['row_count']} row(s) with columns: {columns}."
        if data["truncated"]:
            summary += " Results were truncated at 200 rows."
        return data, summary, None
