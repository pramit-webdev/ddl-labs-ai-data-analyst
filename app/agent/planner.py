"""Deterministic intent router and template answers.

This is the offline mode and the reliability fallback when the LLM is
unavailable or produces an ungrounded answer. It never invents numbers: every
sentence is assembled from tool output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.tools import ToolOutcome, ToolRegistry
from app.analytics import money, num, pct

SUPPORTED_TOPICS = [
    "Revenue, orders, customers and average order value",
    "Monthly revenue and sales trends",
    "Revenue by category and top products",
    "Low-performing products",
    "Customer activity, repeat rate and inactive customers",
    "Payment mix (UPI, cards, COD, wallets)",
    "City-tier and state performance across India",
    "Channel performance (app, website, marketplace, WhatsApp)",
    "Why revenue changed between two months",
    "Festive-season context (Diwali, Shradh)",
    "Charts for revenue, categories, products, customers, payments and tiers",
]

GREETING_RE = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening))\b", re.IGNORECASE)
THANKS_RE = re.compile(r"\b(thanks|thank you|cheers)\b", re.IGNORECASE)


@dataclass
class Intent:
    name: str
    supported: bool = True
    period: str = "last_12_months"
    months: int = 12
    limit: int = 5
    metric: str = "revenue"
    inactive_days: int = 90
    month: str | None = None
    chart_type: str = "line"
    chart_metric: str = "monthly_revenue"
    original: str = ""


def _period(text: str) -> str:
    if re.search(r"\b(previous|prior)\s+month\b", text):
        return "previous_month"
    if re.search(r"\b(last|this|latest)\s+month\b", text):
        return "latest_month"
    if re.search(r"\b(last|past)\s+30\s+days\b", text):
        return "last_30_days"
    if re.search(r"\b(last|past)\s+90\s+days\b", text) or "last quarter" in text:
        return "last_90_days"
    if re.search(r"\b(last|past|trailing)\s+(12|twelve)\s+months\b", text) or "past year" in text or "last year" in text:
        return "last_12_months"
    if "year to date" in text or re.search(r"\bytd\b", text) or "this year" in text:
        return "ytd"
    if "all time" in text or "overall" in text or "since inception" in text:
        return "all_time"
    return "last_12_months"


def _months(text: str) -> int:
    match = re.search(r"\b(\d{1,2})\s*(?:months?|mo)\b", text)
    if match:
        return max(3, min(24, int(match.group(1))))
    if "last year" in text or "past year" in text or "12 months" in text:
        return 12
    return 12


def _limit(text: str, default: int = 5) -> int:
    match = re.search(r"\btop\s+(\d{1,2})\b", text)
    if not match:
        match = re.search(r"\b(\d{1,2})\s+(?:products?|customers?|skus?)\b", text)
    if match:
        return max(1, min(20, int(match.group(1))))
    return default


def _inactive_days(text: str) -> int:
    match = re.search(r"\b(\d{2,3})\s*days?\b", text)
    if match:
        return max(1, min(730, int(match.group(1))))
    return 90


def _month(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})-(\d{2})\b", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b",
        text,
    )
    if match:
        names = ["january", "february", "march", "april", "may", "june",
                 "july", "august", "september", "october", "november", "december"]
        return f"{match.group(2)}-{names.index(match.group(1)) + 1:02d}"
    return None


def _chart(text: str) -> Intent | None:
    if not re.search(r"\b(chart|plot|graph|visuali[sz]e|visuali[sz]ation)\b", text):
        return None
    if "categor" in text:
        metric = "revenue_by_category"
        default_type = "bar"
    elif "payment" in text or "upi" in text or "cod" in text:
        metric = "payment_mix"
        default_type = "doughnut"
    elif "tier" in text or "bharat" in text or "metro" in text:
        metric = "city_tier"
        default_type = "bar"
    elif "channel" in text or "marketplace" in text or "whatsapp" in text:
        metric = "channel"
        default_type = "bar"
    elif "product" in text:
        metric = "top_products"
        default_type = "bar"
    elif "customer" in text or "new vs returning" in text:
        metric = "customer_trend"
        default_type = "bar"
    else:
        metric = "monthly_revenue"
        default_type = "line"
    if re.search(r"\b(pie|donut|doughnut)\b", text):
        chart_type = "doughnut"
    elif re.search(r"\b(line|trend)\b", text):
        chart_type = "line"
    elif re.search(r"\b(bar|column)\b", text):
        chart_type = "bar"
    else:
        chart_type = default_type
    return Intent(
        "chart", period=_period(text), months=_months(text), limit=_limit(text, 8),
        chart_type=chart_type, chart_metric=metric, original=text,
    )


def classify(question: str) -> Intent:
    text = re.sub(r"\s+", " ", (question or "").strip().lower())
    if not text:
        return Intent("unsupported", supported=False, original=question)
    if GREETING_RE.search(text) and len(text) < 60:
        return Intent("greeting", original=question)
    if THANKS_RE.search(text) and len(text) < 60:
        return Intent("thanks", original=question)

    if re.search(r"\b(forecast|predict|projection|next month|next quarter|next year)\b", text):
        return Intent("unsupported", supported=False, original=question)

    chart = _chart(text)
    if chart:
        return chart

    if re.search(r"\b(why|reason|cause|driver|explain)\b", text) and re.search(r"\b(revenue|sales)\b", text) and re.search(
        r"\b(decline|declined|drop|dropped|fall|fell|down|decrease|decreased|slow|soft)\b", text
    ):
        return Intent("decline", period="latest_month", month=_month(text), original=question)
    if "revenue decline" in text or "sales decline" in text or "root cause" in text:
        return Intent("decline", period="latest_month", month=_month(text), original=question)

    if re.search(r"\b(diwali|festive|festival|shradh|seasonal|season)\b", text):
        return Intent("festive", period="latest_month", month=_month(text), original=question)

    if re.search(r"\b(upi|payment mix|payments?|cod|cash on delivery|credit cards?|debit cards?|net banking|wallet)\b", text):
        return Intent("payment_mix", period=_period(text), original=question)

    if re.search(r"\b(city tier|tier 1|tier 2|tier 3|metro|small town|bharat|tier-1|tier-2|tier-3)\b", text):
        return Intent("city_tier_mix", period=_period(text), original=question)

    if re.search(r"\b(channel|marketplace|whatsapp|mobile app|website|retail store)\b", text) and not re.search(r"\b(product|category)\b", text):
        return Intent("channel_performance", period=_period(text), original=question)

    if re.search(r"\b(state|maharashtra|delhi|karnataka|tamil nadu|gujarat|telangana|west bengal|rajasthan|uttar pradesh|kerala)\b", text) and not re.search(r"\b(statement|status)\b", text):
        return Intent("top_states", period=_period(text), limit=_limit(text, 8), original=question)

    if re.search(r"\b(inactive|lapsed|dormant|churn|churned|not purchased|haven't purchased|havent purchased|not bought|no purchase)\b", text) and "customer" in text:
        return Intent("inactive_customers", inactive_days=_inactive_days(text), limit=_limit(text, 10), original=question)
    if "customer" in text and re.search(r"\b(recently|not purchase|purchase recently)\b", text):
        return Intent("inactive_customers", inactive_days=_inactive_days(text), limit=_limit(text, 10), original=question)

    if "customer" in text and re.search(r"\b(top|best|highest|biggest|most valuable)\b", text):
        return Intent("top_customers", period=_period(text), limit=_limit(text), original=question)
    if "customer" in text and re.search(r"\b(trend|new vs returning|acquisition|new customers?|returning customers?)\b", text):
        return Intent("customer_trend", months=_months(text), original=question)
    if "customer" in text or re.search(r"\b(aov|average order value)\b", text):
        if re.search(r"\b(repeat|active|inactive|how many|count|total|new|returning|retention|rate|lifetime)\b", text) or "customer" in text:
            return Intent("customer_metrics", inactive_days=_inactive_days(text), period=_period(text), original=question)

    if "product" in text and re.search(r"\b(low|lowest|worst|underperform|underperforming|weak|slow|bottom|least)\b", text):
        return Intent("low_products", period=_period(text), limit=_limit(text), original=question)
    if "product" in text and re.search(r"\b(top|best|highest|most|selling|bestselling|popular)\b", text):
        return Intent("top_products", period=_period(text), limit=_limit(text), metric="units" if re.search(r"\b(units|quantity|volume)\b", text) else "revenue", original=question)
    if "product" in text and re.search(r"\b(revenue|sales|perform)\b", text):
        return Intent("top_products", period=_period(text), limit=_limit(text), original=question)

    if "categor" in text:
        return Intent("revenue_by_category", period=_period(text), original=question)

    if re.search(r"\b(monthly|by month|month over month|trend|over time|time series)\b", text) and re.search(r"\b(revenue|sales)\b", text):
        return Intent("monthly_revenue", months=_months(text), original=question)
    if re.search(r"\b(revenue|sales|orders|order value|aov|kpi|performance)\b", text):
        return Intent("sales_metrics", period=_period(text), original=question)

    return Intent("unsupported", supported=False, original=question)


def _list_products(rows: list[dict], metric: str) -> str:
    lines = []
    for row in rows:
        value = money(row["revenue"]) if metric == "revenue" else f"{num(row['units'])} units"
        lines.append(f"- **{row['name']}** — {value} from {num(row['orders'])} orders ({row['category']})")
    return "\n".join(lines)


def _list_categories(rows: list[dict]) -> str:
    return "\n".join(
        f"- **{row['category']}** — {money(row['revenue'])} ({row['share_pct']:.1f}% of revenue)"
        for row in rows
    )


def _list_inactive(data: dict) -> str:
    lines = []
    for row in data["customers"][:5]:
        lines.append(
            f"- **{row['name']}** ({row['segment']}, {row['country']}) — "
            f"{money(row['lifetime_revenue'])} lifetime, last order {row['last_order']} "
            f"({row['days_since_last_order']} days ago)"
        )
    return "\n".join(lines)


def _list_payments(rows: list[dict]) -> str:
    return "\n".join(
        f"- **{r['payment_method']}** — {money(r['revenue'])} ({r['share_pct']:.1f}% of revenue) "
        f"across {num(r['orders'])} orders"
        for r in rows
    )


def _list_tiers(rows: list[dict]) -> str:
    return "\n".join(
        f"- **{r['city_tier']}** — {money(r['revenue'])} ({r['share_pct']:.1f}% of revenue) "
        f"from {num(r['customers'])} customers"
        for r in rows
    )


def _list_states(rows: list[dict]) -> str:
    return "\n".join(
        f"{r['rank']}. **{r['state']}** — {money(r['revenue'])} from {num(r['orders'])} orders"
        for r in rows
    )


def _list_channels(rows: list[dict]) -> str:
    return "\n".join(
        f"- **{r['channel']}** — {money(r['revenue'])} ({r['share_pct']:.1f}% of revenue), "
        f"AOV {money(r['aov'])}"
        for r in rows
    )


def build_deterministic(intent: Intent, registry: ToolRegistry, *, mode: str = "offline", notice: str | None = None) -> dict[str, Any]:
    outcomes: list[ToolOutcome] = []
    chart: dict[str, Any] | None = None
    supported = intent.supported
    name = intent.name

    def run(tool: str, **args: Any) -> ToolOutcome:
        outcome = registry.execute(tool, args)
        outcomes.append(outcome)
        return outcome

    if name == "greeting":
        answer = (
            "Hi — I'm Atlas, your AI business analyst. I can answer questions about revenue, orders, "
            "customers, products and categories from the warehouse, and I can build charts. "
            "Try: “What were the top 5 products by revenue?” or “Why did revenue decline last month?”"
        )
    elif name == "thanks":
        answer = "You're welcome. Ask me another question whenever you're ready."
    elif not supported:
        answer = (
            "I can't answer that from the available business data. I don't have information about "
            "forecasts, external markets, inventory, profitability or events outside this warehouse.\n\n"
            "**I can help with:**\n"
            + "\n".join(f"- {topic}" for topic in SUPPORTED_TOPICS)
            + "\n\nTry: “Which category generated the most revenue?” or “Which customers have not purchased recently?”"
        )
    elif name == "payment_mix":
        outcome = run("get_payment_mix", period=intent.period)
        if not outcome.ok:
            answer = f"I couldn't load the payment mix: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            answer = (
                f"## Payment mix — {intent.period.replace('_', ' ')}\n\n"
                + _list_payments(rows)
            )
    elif name == "city_tier_mix":
        outcome = run("get_city_tier_mix", period=intent.period)
        if not outcome.ok:
            answer = f"I couldn't load the city-tier mix: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            tier2 = next((r for r in rows if r["city_tier"] == "Tier 2"), None)
            tier3 = next((r for r in rows if r["city_tier"] == "Tier 3"), None)
            answer = (
                f"## City-tier mix — {intent.period.replace('_', ' ')}\n\n"
                + _list_tiers(rows)
            )
            if tier2 and tier3:
                answer += (
                    f"\n\nTier 2 contributes **{tier2['share_pct']:.1f}%** and Tier 3 "
                    f"**{tier3['share_pct']:.1f}%** of revenue — the 'Bharat' opportunity "
                    "beyond the metros."
                )
    elif name == "channel_performance":
        outcome = run("get_channel_performance", period=intent.period)
        if not outcome.ok:
            answer = f"I couldn't load channel performance: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            answer = (
                f"## Channel performance — {intent.period.replace('_', ' ')}\n\n"
                + _list_channels(rows)
            )
    elif name == "top_states":
        outcome = run("get_top_states", period=intent.period, limit=intent.limit)
        if not outcome.ok:
            answer = f"I couldn't load state performance: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            answer = (
                f"## Top states by revenue — {intent.period.replace('_', ' ')}\n\n"
                + _list_states(rows)
            )
    elif name == "festive":
        outcome = run("get_revenue_decline_analysis", month=intent.month)
        if not outcome.ok:
            answer = f"I couldn't load the festive analysis: {outcome.error}"
            supported = False
        else:
            data = outcome.data
            lines = [
                f"## Festive context — {data['month']}",
                "",
                f"Revenue was **{money(data['current']['revenue'])}** across "
                f"{num(data['current']['orders'])} orders "
                f"(AOV {money(data['current']['aov'])}).",
                "",
                "**What the data shows**",
            ]
            lines += [f"- {line}" for line in data["summary_lines"][:5]]
            if data.get("context_notes"):
                lines += ["", "**Calendar context**"] + [f"- {note}" for note in data["context_notes"]]
            lines += [
                "",
                "**Read-through:** treat September softness as a mix shift ahead of the "
                "October-November festive window, not necessarily a demand collapse. "
                "The categories to watch are the high-ticket ones that paused.",
            ]
            answer = "\n".join(lines)
    elif name == "decline":
        outcome = run("get_revenue_decline_analysis", month=intent.month)
        if not outcome.ok:
            answer = f"I couldn't run the decline analysis: {outcome.error}"
            supported = False
        else:
            data = outcome.data
            change = data["change"]
            lines = [
                f"## Revenue decline: {data['previous_month']} → {data['month']}",
                "",
                f"Revenue **{money(data['current']['revenue'])}** vs **{money(data['previous']['revenue'])}** "
                f"({money(change['revenue_delta'])}, {change['revenue_delta_pct']:+.1f}%).",
                "",
                "**What drove it**",
            ]
            for line in data["summary_lines"]:
                lines.append(f"- {line}")
            decomp = data["decomposition"]
            if abs(decomp["volume_effect"]) >= abs(decomp["aov_effect"]):
                driver = "order volume"
            else:
                driver = "average order value"
            if data["negative_categories"]:
                top_driver = data["negative_categories"][0]
                lines += [
                    "",
                    f"**Bottom line:** the change was primarily driven by {driver}. "
                    f"{top_driver['name']} accounted for "
                    f"{abs(top_driver['share_of_change_pct']):.1f}% of the decline "
                    f"({money(top_driver['delta'])}).",
                ]
            else:
                lines += ["", f"**Bottom line:** the change was primarily driven by {driver}."]
            if data.get("context_notes"):
                lines += ["", "**Calendar context**"] + [f"- {note}" for note in data["context_notes"]]
            answer = "\n".join(lines)
    elif name == "inactive_customers":
        outcome = run("get_inactive_customers", inactive_days=intent.inactive_days, limit=intent.limit)
        if not outcome.ok:
            answer = f"I couldn't load inactive customers: {outcome.error}"
            supported = False
        else:
            data = outcome.data
            answer = (
                f"## Customers inactive for {data['inactive_days']}+ days\n\n"
                f"{num(data['total_inactive'])} customers have not purchased since {data['cutoff']}. "
                f"Here are the highest-value lapsed customers:\n\n{_list_inactive(data)}"
            )
    elif name == "top_customers":
        outcome = run("get_top_customers", period=intent.period, limit=intent.limit)
        if not outcome.ok:
            answer = f"I couldn't load top customers: {outcome.error}"
            supported = False
        else:
            data = outcome.data
            lines = [f"## Top customers — {intent.period.replace('_', ' ')}", ""]
            for row in data:
                lines.append(
                    f"{row['rank']}. **{row['name']}** ({row['segment']}, {row['country']}) — "
                    f"{money(row['revenue'])} from {num(row['orders'])} orders"
                )
            answer = "\n".join(lines)
    elif name == "customer_trend":
        outcome = run("get_customer_trend", months=intent.months)
        if not outcome.ok:
            answer = f"I couldn't load the customer trend: {outcome.error}"
            supported = False
        else:
            data = outcome.data
            latest = data[-1]
            answer = (
                f"## Customer trend — last {len(data)} months\n\n"
                f"In **{latest['full_label'] if 'full_label' in latest else latest['label']}**, "
                f"{num(latest['new_customers'])} new customers and {num(latest['returning_customers'])} "
                f"returning customers purchased. New customers generated {money(latest['new_revenue'])} "
                f"and returning customers generated {money(latest['returning_revenue'])}.\n\n"
                f"Use the chart to see the monthly pattern."
            )
    elif name == "customer_metrics":
        outcome = run("get_customer_metrics", inactive_days=intent.inactive_days)
        if not outcome.ok:
            answer = f"I couldn't load customer metrics: {outcome.error}"
            supported = False
        else:
            d = outcome.data
            answer = (
                f"## Customer health\n\n"
                f"- **{num(d['total_customers'])}** customers in total; **{num(d['purchasing_customers'])}** have purchased.\n"
                f"- **{num(d['active_customers'])}** active in the last {d['inactive_days']} days; "
                f"**{num(d['inactive_customers'])}** inactive ({pct(d['inactive_rate_pct'])}).\n"
                f"- **{num(d['repeat_customers'])}** repeat customers ({pct(d['repeat_rate_pct'])} repeat rate).\n"
                f"- **{num(d['never_purchased'])}** customers have never purchased.\n"
                f"- Average orders per purchasing customer: **{d['avg_orders_per_purchasing_customer']}**; "
                f"average lifetime revenue: **{money(d['avg_lifetime_revenue'])}**."
            )
    elif name == "low_products":
        outcome = run("get_low_performing_products", period=intent.period, limit=intent.limit)
        if not outcome.ok:
            answer = f"I couldn't load product performance: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            answer = (
                f"## Lowest-revenue active products — {intent.period.replace('_', ' ')}\n\n"
                + "\n".join(
                    f"- **{r['name']}** ({r['category']}) — {money(r['revenue'])} from {num(r['units'])} units"
                    for r in rows
                )
            )
    elif name == "top_products":
        outcome = run("get_top_products", period=intent.period, limit=intent.limit, metric=intent.metric)
        if not outcome.ok:
            answer = f"I couldn't load top products: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            answer = (
                f"## Top {len(rows)} products by {intent.metric} — {intent.period.replace('_', ' ')}\n\n"
                + _list_products(rows, intent.metric)
            )
    elif name == "revenue_by_category":
        outcome = run("get_revenue_by_category", period=intent.period)
        if not outcome.ok:
            answer = f"I couldn't load category revenue: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            answer = (
                f"## Revenue by category — {intent.period.replace('_', ' ')}\n\n"
                + _list_categories(rows)
            )
    elif name == "monthly_revenue":
        outcome = run("get_monthly_revenue", months=intent.months)
        if not outcome.ok:
            answer = f"I couldn't load monthly revenue: {outcome.error}"
            supported = False
        else:
            rows = outcome.data
            recent = rows[-3:]
            answer = (
                f"## Monthly revenue — last {len(rows)} months\n\n"
                + "\n".join(
                    f"- **{r['full_label']}** — {money(r['revenue'])} from {num(r['orders'])} orders "
                    f"(AOV {money(r['aov'])})"
                    for r in recent
                )
                + "\n\nUse the chart for the full trend."
            )
    elif name == "chart":
        outcome = run(
            "generate_chart",
            chart_type=intent.chart_type,
            metric=intent.chart_metric,
            period=intent.period,
            months=intent.months,
            limit=intent.limit,
        )
        if not outcome.ok:
            answer = f"I couldn't generate the chart: {outcome.error}"
            supported = False
        else:
            chart = outcome.chart
            answer = f"Here is the chart you asked for: **{chart['title']}**. {outcome.summary}"
    else:  # sales_metrics
        outcome = run("get_sales_metrics", period=intent.period)
        if not outcome.ok:
            answer = f"I couldn't load sales metrics: {outcome.error}"
            supported = False
        else:
            k = outcome.data["kpis"]
            rev = k["revenue"]
            trend = ""
            if rev["delta_pct"] is not None:
                trend = f" Revenue is {rev['delta_pct']:+.1f}% versus the previous period."
            answer = (
                f"## Sales metrics — {intent.period.replace('_', ' ')}\n\n"
                f"- **Revenue:** {money(rev['value'])}{trend}\n"
                f"- **Orders:** {num(k['orders']['value'])}\n"
                f"- **Purchasing customers:** {num(k['customers']['value'])}\n"
                f"- **Average order value:** {money(k['aov']['value'])}"
            )

    if outcomes:
        chart = chart or next((o.chart for o in outcomes if o.chart), None)
    evidence = [o.data for o in outcomes if o.ok]
    return {
        "answer": answer.strip(),
        "outcomes": outcomes,
        "chart": chart,
        "supported": supported,
        "mode": mode,
        "notice": notice,
        "evidence": evidence,
    }
