"""LLM-as-judge for qualitative answer evaluation.

Uses Groq Llama 3.3 70B as the judge with an explicit 4-point rubric.
Calibrated against human ratings to detect and correct for self-preference
bias (same model family generates and judges the answers).

Rubric (0-3)
-----------
  3. Correct and complete. Every factual claim is accurate. Citations resolve.
      Numeric values match the gold answer within 0.5%.
  2. Mostly correct. Minor inaccuracies (e.g, off-by-one year) that don't
      materially affect the answer. No hallucinated numbers.
  1. Partially correct. Contains some accurate information but also at least
      one material factual error or missing component.
  0. Incorrect or unhelpful. Hallucinated numbers, wrong company, wrong year,
      or refusal to answer.

Calibration
-----------
Calibration computes Cohen's κ between the LLM judge and human ratings on a
30-question calibration set (provided separately by the author). If κ < 0.4
(fair agreement), the judge scores are not published until the judge is
improved or the rubric is revised.

Self-preference bias: since the generator (Llama 3.3 70B) and judge are the
same model family, the calibration step is especially important. The bias
is expected to manifest as the judge being lenient on Llama-generated answers;
the calibration κ catches this systematically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backend.agent.llm import get_llm
from backend.logging_config import get_logger

log = get_logger(__name__)

JUDGE_RUBRIC = """You are an expert financial analyst evaluating the quality of answers to SEC filing questions.

Score the answer 0-3 using this rubric:
  3. Fully correct and complete. Every factual claim is verifiably accurate.
      Numbers match the gold answer within 0.5%. All relevant aspects addressed.
  2. Mostly correct. Minor inaccuracy (e.g, wrong quarter, slightly off number)
      that doesn't materially mislead. Core answer is right.
  1. Partially correct. Captures some truth but has at least one material
      factual error or misses a key component.
  0. Incorrect or unhelpful. Hallucinated numbers, wrong company/year,
      evasion, or refusal.

Rules:
  - Numbers must be within 0.5% of gold to count as correct.
  - A missing citation is NOT a quality failure unless the answer is wrong.
  - Score the factual content, not writing style.

Respond with exactly this JSON (no extra text):
{"score": <0|1|2|3>, "rationale": "<one sentence explaining the score>"}
"""

JUDGE_QUESTION_TEMPLATE = """Question: {question}

Gold answer: {gold_answer}

Model answer: {model_answer}

Score now.
"""


@dataclass
class JudgeResult:
    score: int                   # 0-3
    rationale: str
    model: str
    provider: str


async def judge_answer(
    question: str,
    gold_answer: str | None,
    model_answer: str,
) -> JudgeResult:
    """Score a single question-answer pair against the gold using the LLM judge."""
    if not gold_answer:
        # No gold answer to compare against, skip judge.
        return JudgeResult(score=-1, rationale="no gold answer provided", model="", provider="")

    llm = get_llm()

    class _JudgeResponse:
        """Mini Pydantic-like schema just for JSON parsing."""
        score: int
        rationale: str

        @classmethod
        def model_validate_json(cls, text: str) -> "_JudgeResponse":  # type: ignore[override]
            from pydantic import BaseModel

            class _M(BaseModel):
                score: int
                rationale: str

            m = _M.model_validate_json(text)
            obj = cls.__new__(cls)
            obj.score = m.score
            obj.rationale = m.rationale
            return obj

    from pydantic import BaseModel

    class _JudgeModel(BaseModel):
        score: int
        rationale: str

    result, resp = await llm.chat_json(
        messages=[
            {"role": "system", "content": JUDGE_RUBRIC},
            {
                "role": "user",
                "content": JUDGE_QUESTION_TEMPLATE.format(
                    question=question,
                    gold_answer=gold_answer,
                    model_answer=model_answer,
                ),
            },
        ],
        schema=_JudgeModel,
        model_role="primary",
        temperature=0.0,
        max_tokens=256,
    )

    # Clamp to valid range
    score = max(0, min(3, result.score))
    log.debug("judge.scored", question=question[:60], score=score, provider=resp.provider)
    return JudgeResult(
        score=score,
        rationale=result.rationale,
        model=resp.model,
        provider=resp.provider,
    )


# ---------------------------------------------------------------------------
# Calibration. Cohen's κ vs human ratings
# ---------------------------------------------------------------------------


def cohens_kappa(
    rater_a: list[int],
    rater_b: list[int],
    n_categories: int = 4,
) -> float:
    """Compute Cohen's κ (unweighted) for two raters on n_categories outcomes.

    Args:
        rater_a: list of integer scores (0-3) from the first rater (e.g, human).
        rater_b: list of integer scores (0-3) from the second rater (e.g. LLM judge).
        n_categories: number of distinct score levels.

    Returns:
        κ in [-1, 1]. Interpretation:
          < 0.0 . less than chance
          0.0-0.2. slight
          0.2-0.4. fair
          0.4-0.6. moderate  ← minimum acceptable for publication
          0.6-0.8. substantial
          0.8-1.0. almost perfect
    """
    if len(rater_a) != len(rater_b):
        raise ValueError("rater lists must have the same length")
    n = len(rater_a)
    if n == 0:
        raise ValueError("empty rater lists")

    # Observed agreement
    p_o = sum(a == b for a, b in zip(rater_a, rater_b)) / n

    # Expected agreement by chance
    p_e = 0.0
    cats = range(n_categories)
    for cat in cats:
        p_a = rater_a.count(cat) / n
        p_b = rater_b.count(cat) / n
        p_e += p_a * p_b

    if p_e == 1.0:
        return 1.0  # perfect agreement by construction
    return (p_o - p_e) / (1.0 - p_e)


@dataclass
class CalibrationReport:
    kappa: float
    n_samples: int
    human_scores: list[int]
    judge_scores: list[int]
    agreement_pct: float
    interpretation: str

    def passes_threshold(self, min_kappa: float = 0.4) -> bool:
        return self.kappa >= min_kappa

    def to_markdown(self) -> str:
        lines = [
            f"**Cohen's κ**: {self.kappa:.3f} ({self.interpretation})",
            f"**N samples**: {self.n_samples}",
            f"**Exact agreement**: {self.agreement_pct:.1%}",
            "",
            "| Score | Human count | Judge count |",
            "|---|---|---|",
        ]
        for s in range(4):
            hc = self.human_scores.count(s)
            jc = self.judge_scores.count(s)
            lines.append(f"| {s} | {hc} | {jc} |")
        if not self.passes_threshold():
            lines.append(
                "\n⚠️ κ below 0.4 (fair agreement). judge scores not published "
                "until rubric is revised or a different judge model is selected."
            )
        return "\n".join(lines)


async def calibrate_judge(
    calibration_set: list[dict[str, Any]],
) -> CalibrationReport:
    """Run the LLM judge on the calibration set and compare to human scores.

    Args:
        calibration_set: list of dicts with keys:
          - question: str
          - gold_answer: str
          - model_answer: str
          - human_score: int (0-3)

    Returns: CalibrationReport with κ and score breakdown.
    """
    human_scores: list[int] = []
    judge_scores: list[int] = []

    for i, item in enumerate(calibration_set):
        result = await judge_answer(
            question=item["question"],
            gold_answer=item.get("gold_answer"),
            model_answer=item["model_answer"],
        )
        human_scores.append(item["human_score"])
        judge_scores.append(result.score)
        log.info(
            "calibration.scored",
            i=i + 1,
            n=len(calibration_set),
            human=item["human_score"],
            judge=result.score,
        )

    kappa = cohens_kappa(human_scores, judge_scores)
    agreement_pct = sum(a == b for a, b in zip(human_scores, judge_scores)) / len(human_scores)

    if kappa >= 0.8:
        interpretation = "almost perfect"
    elif kappa >= 0.6:
        interpretation = "substantial"
    elif kappa >= 0.4:
        interpretation = "moderate"
    elif kappa >= 0.2:
        interpretation = "fair"
    elif kappa >= 0.0:
        interpretation = "slight"
    else:
        interpretation = "less than chance"

    return CalibrationReport(
        kappa=kappa,
        n_samples=len(calibration_set),
        human_scores=human_scores,
        judge_scores=judge_scores,
        agreement_pct=agreement_pct,
        interpretation=interpretation,
    )
