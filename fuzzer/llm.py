
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

# Assignment constraints.
MAX_ITERATIONS = 5
MAX_USD = 5.00

DEFAULT_MODEL = "gemini-2.5-flash"


@dataclass(frozen=True)
class ModelPricing:
    """0.30USD per 1,000,000 tokens."""

    input_per_mtok: float
    output_per_mtok: float


# Fill in from current Vertex pricing for whichever model you settle on, e.g.
#   PRICING["gemini-2.5-flash"] = ModelPricing(input_per_mtok=..., output_per_mtok=...)
# Left empty deliberately -- see the module docstring.
PRICING: dict[str, ModelPricing] = {}


@dataclass
class Usage:
    """What one call cost, in the units we can actually observe."""

    model: str
    input_tokens: int
    output_tokens: int
    seconds: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_usd(self) -> float | None:
        price = PRICING.get(self.model)
        if price is None:
            return None
        return (
            self.input_tokens * price.input_per_mtok
            + self.output_tokens * price.output_per_mtok
        ) / 1_000_000


class BudgetExceeded(RuntimeError):
    """Raised instead of making a call that would overrun the budget."""


@dataclass
class Budget:
    """Runs out before the money does, and says which limit stopped it."""

    max_iterations: int = MAX_ITERATIONS
    max_usd: float = MAX_USD
    iterations_used: int = 0
    calls: list[Usage] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.calls)

    @property
    def spent_usd(self) -> float | None:
        """None if any call used an unpriced model -- a partial total would
        understate spend, and understating it is the failure that matters."""
        amounts = [u.estimated_usd for u in self.calls]
        if any(a is None for a in amounts):
            return None
        return sum(amounts)

    def check(self) -> None:
        if self.iterations_used >= self.max_iterations:
            raise BudgetExceeded(
                f"iteration cap reached ({self.iterations_used}/{self.max_iterations})"
            )
        spent = self.spent_usd
        if spent is not None and spent >= self.max_usd:
            raise BudgetExceeded(f"spend cap reached (${spent:.2f}/${self.max_usd:.2f})")

    def record(self, usage: Usage) -> None:
        self.calls.append(usage)

    def report(self) -> str:
        spent = self.spent_usd
        if not PRICING:
            # Guard against a reassuring "$0.0000" that only means nobody has
            # entered a price. The report needs to say "unknown", not "free".
            cost = "unpriced -- set fuzzer.llm.PRICING to get a dollar figure"
        elif spent is None:
            cost = "partly unpriced -- some calls used a model with no price set"
        else:
            cost = f"${spent:.4f}"
        lines = [
            f"iterations : {self.iterations_used}/{self.max_iterations}",
            f"API calls  : {len(self.calls)}",
            f"tokens     : {self.total_tokens} "
            f"(in {sum(u.input_tokens for u in self.calls)}, "
            f"out {sum(u.output_tokens for u in self.calls)})",
            f"est. cost  : {cost}",
        ]
        return "\n".join(lines)


class LLM:
    """Thin wrapper over the Vertex client. Auth is ADC; see .env."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        project: str | None = None,
        location: str | None = None,
        budget: Budget | None = None,
    ):
        project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        location = location or os.environ.get("GOOGLE_CLOUD_LOCATION")
        if not project or not location:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION must be set; "
                "load .env first (load_dotenv('.env'))"
            )
        # vertexai=True is required. Without it the client looks for an API key
        # and fails even when ADC is perfectly healthy -- the two auth paths do
        # not mix.
        self.client = genai.Client(vertexai=True, project=project, location=location)
        self.model = model
        self.budget = budget or Budget()

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 1.0,
        max_output_tokens: int | None = None,
    ) -> tuple[str, Usage]:
        """One call. Raises BudgetExceeded rather than overrunning."""
        self.budget.check()

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=system,
        )

        started = time.perf_counter()
        response = self.client.models.generate_content(
            model=self.model, contents=prompt, config=config
        )
        elapsed = time.perf_counter() - started

        meta = getattr(response, "usage_metadata", None)
        usage = Usage(
            model=self.model,
            input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
            output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
            seconds=elapsed,
        )
        self.budget.record(usage)
        return (response.text or ""), usage
