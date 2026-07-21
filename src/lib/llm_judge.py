import logging

from ..lib.output_eval_agent import MultiEvaluator
from strands.models.openai import OpenAIModel

logger = logging.getLogger(__name__)


def _build_system_prompt(metrics: list[dict]) -> str:
    """Build a scoring prompt from a list of metric definitions."""
    metric_lines = []
    for idx, metric in enumerate(metrics, start=1):
        metric_lines.append(
            f"{idx}. {metric['key']}: {metric['name']}\n   {metric['rubric']}"
        )

    score_example = ",\n    ".join(f'"{m["key"]}": 7.5' for m in metrics)

    return f"""\
Score the candidate 0.0–10.0 on each of these dimensions:

{chr(10).join(metric_lines)}

Return ONLY valid JSON, no explanation outside the JSON, in exactly this shape:
{{
  "scores": {{
    {score_example}
  }},
  "summary": "2-3 sentence plain English summary of the candidate's performance",
  "red_flags": ["list of specific concerning behaviors observed, empty array if none"]
}}

Be strict and evidence-based. Reference specific events from the log in your summary.

IMPORTANT: Do NOT show your reasoning, thinking process, or chain-of-thought.
Output ONLY the final JSON object. No markdown fences, no explanations, no preamble.
"""


def _normalize_model_id(model: str) -> str:
    return model.rsplit("/", 1)[-1].strip()


def multi_judge_session(
    logs,
    problem_statement: str,
    files_path: str,
    output_metrics: list[dict],
    interact_metrics: list[dict],
):
    from .. import config as _cfg

    model = OpenAIModel(
        model_id=_normalize_model_id(_cfg.SCORING_MODEL),
        client_args={
            "base_url": _cfg.SCORING_BASE_URL,
            "api_key": _cfg.SCORING_API_KEY,
        },
        params={"temperature": 1, "stream": False},
    )
    mult_eval = MultiEvaluator(
        logs, files_path, problem_statement,
        _build_system_prompt(output_metrics),
        _build_system_prompt(interact_metrics),
        model, model, model,
    )
    mult_eval.eval_output()
    mult_eval.eval_turns_human_candidate()
    return mult_eval.eval_final()
