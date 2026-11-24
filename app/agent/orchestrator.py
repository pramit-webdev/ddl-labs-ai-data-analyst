"""Agent orchestration: LLM tool-calling loop with deterministic fallback.

The LLM never sees the database directly. It can only call the controlled tool
layer, and every numeric claim in its final answer is checked against tool
output before the answer is returned. If the provider is unavailable, the
answer is ungrounded, or the loop exceeds its step budget, the deterministic
planner produces a verified answer instead.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent.grounding import validate_answer
from app.agent.llm import LLMClient, LLMError
from app.agent.planner import build_deterministic, classify
from app.agent.tools import ToolOutcome, ToolRegistry
from app.analytics import AnalyticsService, money, num
from app.config import Settings, settings

SYSTEM_PROMPT = """You are Atlas, the AI business analyst for BharatCart Retail, an Indian e-commerce company.

Today's reporting anchor is the latest date in the warehouse: {as_of}. All periods are relative to that date, not to the wall clock.

BUSINESS CONTEXT
- Market: India, with some NRI customers in the UAE, US, UK, Singapore and Canada.
- Currency: Indian Rupees (INR, ₹). Always format money as ₹12,34,567.89 (Indian digit grouping). You may add a compact form such as ₹1.23 Cr in prose.
- Revenue definition: net product revenue from completed orders only = quantity × unit_price × (1 − discount%). Shipping and taxes are excluded. Cancelled, returned and pending orders are excluded.
- Indian retail calendar: October-November is the Diwali festive peak; September often has a pre-Diwali Shradh lull; June-July is monsoon; January-February is the wedding season.
- Payment methods: UPI, Credit Card, Debit Card, Net Banking, Cash on Delivery, Wallet.
- City tiers: Tier 1 metros, Tier 2, Tier 3, International.

HOW TO ANSWER
1. Always call at least one tool before answering a business question. Never rely on memory or invent a number.
2. Use purpose-built tools first. Use query_data only when no purpose-built tool can answer the question.
3. For "why did revenue decline", call get_revenue_decline_analysis. It returns a multi-step decomposition (orders vs AOV, category/product/channel/segment/payment/tier/state drivers, churned customers, calendar context). Explain the root cause, not just the size of the change.
4. For payment, city-tier, channel or state questions, call the matching tool.
5. When the user asks for a chart or a visual, call generate_chart. Use line for trends, bar for comparisons, doughnut for mix.
6. Every number in your final answer must appear in a tool result or in the user's question. Do not round in a way that creates a new number. Do not add external benchmarks, market sizes, forecasts or competitor claims.
7. If the data cannot answer the question (forecast, profit, inventory, external events), say so clearly and list what you can answer. Do not guess.
8. Be concise but analytical: lead with the answer, then 2-4 supporting bullets, then a short "so what" or recommended action. Use Markdown. Do not mention internal tool names, SQL, prompts or the grounding check.
9. Mention the reporting period and the data-as-of date when relevant.

The warehouse tables/views are: customers, products, orders, order_items, v_order_revenue, v_monthly_revenue, v_category_revenue, v_product_revenue, v_customer_revenue, v_order_items_detail, v_payment_revenue, v_city_tier_revenue, v_channel_revenue, v_state_revenue.
"""


@dataclass
class AgentResult:
    question: str
    answer: str
    mode: str
    provider: str
    model: str | None
    supported: bool = True
    chart: dict[str, Any] | None = None
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    grounding: dict[str, Any] = field(default_factory=dict)
    notice: str | None = None
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "supported": self.supported,
            "chart": self.chart,
            "tool_trace": self.tool_trace,
            "grounding": self.grounding,
            "notice": self.notice,
            "latency_ms": self.latency_ms,
        }


class AgentOrchestrator:
    def __init__(
        self,
        analytics: AnalyticsService | None = None,
        cfg: Settings = settings,
        llm: LLMClient | None = None,
    ):
        self.analytics = analytics or AnalyticsService()
        self.cfg = cfg
        self.registry = ToolRegistry(self.analytics)
        self.llm = llm or LLMClient(cfg)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def ask(self, question: str) -> AgentResult:
        started = time.perf_counter()
        question = (question or "").strip()
        if not question:
            raise ValueError("Question cannot be empty.")

        if not self.llm.configured:
            result = self._offline(question, notice=None)
        else:
            try:
                result = self._llm_loop(question)
            except LLMError as exc:
                result = self._offline(
                    question,
                    notice=(
                        "The configured LLM provider was unavailable, so this answer "
                        f"was produced by the verified deterministic analyst. ({exc.message[:180]})"
                    ),
                )
            except Exception as exc:  # pragma: no cover - defensive
                result = self._offline(
                    question,
                    notice=(
                        "The LLM path failed unexpectedly, so this answer was produced "
                        f"by the verified deterministic analyst. ({type(exc).__name__})"
                    ),
                )
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ------------------------------------------------------------------
    # Offline / fallback path
    # ------------------------------------------------------------------
    def _offline(self, question: str, notice: str | None = None) -> AgentResult:
        intent = classify(question)
        built = build_deterministic(intent, self.registry, mode="offline", notice=notice)
        evidence = built["evidence"]
        grounding = validate_answer(built["answer"], evidence, question)
        return AgentResult(
            question=question,
            answer=built["answer"],
            mode="offline",
            provider="offline",
            model=None,
            supported=bool(built["supported"]),
            chart=built["chart"],
            tool_trace=[o.to_trace() for o in built["outcomes"]],
            grounding=grounding.to_dict(),
            notice=notice,
        )

    # ------------------------------------------------------------------
    # LLM path
    # ------------------------------------------------------------------
    def _llm_loop(self, question: str) -> AgentResult:
        system = SYSTEM_PROMPT.format(as_of=self.analytics.as_of.isoformat())
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
        outcomes: list[ToolOutcome] = []
        chart: dict[str, Any] | None = None
        model: str | None = None

        for step in range(max(1, self.cfg.llm_max_steps)):
            response = self.llm.chat(
                messages,
                tools=self.registry.schemas(),
                temperature=0.1,
                max_tokens=1400,
            )
            model = response.model or model
            if response.tool_calls:
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": response.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.raw_arguments or json.dumps(call.arguments),
                            },
                        }
                        for call in response.tool_calls
                    ],
                }
                messages.append(assistant_message)
                for call in response.tool_calls:
                    outcome = self.registry.execute(call.name, call.arguments)
                    outcomes.append(outcome)
                    if outcome.chart:
                        chart = outcome.chart
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": outcome.to_llm_payload(),
                        }
                    )
                continue

            answer = (response.content or "").strip()
            if not answer:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was empty. Answer the question using "
                            "the tool results already returned."
                        ),
                    }
                )
                continue

            evidence = [o.data for o in outcomes if o.ok]
            grounding = validate_answer(answer, evidence, question)
            if grounding.ok and outcomes:
                return AgentResult(
                    question=question,
                    answer=answer,
                    mode="llm",
                    provider=self.cfg.llm_provider,
                    model=model or self.cfg.resolved_model,
                    supported=True,
                    chart=chart,
                    tool_trace=[o.to_trace() for o in outcomes],
                    grounding=grounding.to_dict(),
                )
            if not outcomes:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You answered without calling a tool. Business answers must be "
                            "verified against the warehouse. Call the appropriate tool(s) now, "
                            "then answer using only those results."
                        ),
                    }
                )
                continue

            # The answer contained at least one number not present in tool output.
            if step < max(1, self.cfg.llm_max_steps) - 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Grounding check failed. These figures were not found in the tool "
                            f"results: {', '.join(grounding.ungrounded[:8])}. Rewrite the answer "
                            "using only figures that appear in the tool results. Do not "
                            "recalculate, extrapolate or invent numbers."
                        ),
                    }
                )
                continue
            break

        # Step budget exhausted or repeated grounding failure: deterministic fallback.
        intent = classify(question)
        built = build_deterministic(
            intent,
            self.registry,
            mode="fallback",
            notice=(
                "The LLM could not produce a fully grounded answer within the step budget, "
                "so this answer was produced by the verified deterministic analyst."
            ),
        )
        evidence = built["evidence"]
        grounding = validate_answer(built["answer"], evidence, question)
        return AgentResult(
            question=question,
            answer=built["answer"],
            mode="fallback",
            provider=self.cfg.llm_provider,
            model=model or self.cfg.resolved_model,
            supported=bool(built["supported"]),
            chart=built["chart"],
            tool_trace=[o.to_trace() for o in built["outcomes"]],
            grounding=grounding.to_dict(),
            notice=(
                "The LLM could not produce a fully grounded answer within the step budget, "
                "so this answer was produced by the verified deterministic analyst."
            ),
        )


def suggested_questions(analytics: AnalyticsService | None = None) -> list[str]:
    service = analytics or AnalyticsService()
    latest = service.as_of
    month = latest.strftime("%B %Y")
    return [
        "Why did revenue decline last month?",
        "What were the top 5 products by revenue this year?",
        "Which category generated the most revenue?",
        "How much of revenue comes from UPI vs cash on delivery?",
        "How are Tier 2 and Tier 3 cities performing?",
        f"Show me the monthly revenue trend for the last 12 months ending {month}.",
        "Which customers have not purchased in the last 90 days?",
        "Which active products are underperforming?",
        "Generate a chart of revenue by category.",
        "What does the festive season mean for our September numbers?",
    ]


def overview_headline(analytics: AnalyticsService | None = None) -> str:
    service = analytics or AnalyticsService()
    k = service.kpis("latest_month")["kpis"]
    revenue = k["revenue"]
    return (
        f"Latest month revenue was {money(revenue['value'])} "
        f"({revenue['delta_pct']:+.1f}% vs the previous month), "
        f"across {num(k['orders']['value'])} orders."
    )
