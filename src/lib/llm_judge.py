import json
import logging
import re

import httpx
from fastapi import HTTPException

from ..config import (
    CODEVECTOR_API_KEY,
    CODEVECTOR_BASE_URL,
    CODEVECTOR_MAX_TOKENS,
    CODEVECTOR_MODEL,
    CODEVECTOR_TEMPERATURE,
    CODEVECTOR_TIMEOUT_SECONDS,
)
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

    metric_keys = ", ".join(f'"{m["key"]}"' for m in metrics)
    score_example = ",\n    ".join(
        f'"{m["key"]}": 7.5' for m in metrics
    )
    
#     You are an expert technical interviewer evaluating how a candidate used AI coding
# assistance during a timed coding interview. You will receive the full session log
# including every prompt they sent to the AI, every LLM response, all terminal
# commands and outputs, and every code change they accepted or rejected.

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


def _extract_json_from_reasoning(reasoning: str) -> str | None:
    """Best-effort extraction of a JSON object from reasoning text."""
    matches = list(re.finditer(r"\{[\s\S]*?\}", reasoning))
    for match in reversed(matches):
        candidate = match.group(0)
        if '"scores"' in candidate and '"summary"' in candidate:
            return candidate
    return None


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _normalize_model_id(model: str) -> str:
    """Return the bare model ID without a provider prefix.

    The OpenCode AI SDK sends only the model name to the gateway (e.g.
    'kimi-k2.6'), not the fully-qualified 'provider/model' form used in
    opencode.json. Strip any prefix so the backend matches that behavior.
    """
    return model.rsplit("/", 1)[-1].strip()


def _build_request_body(system_prompt: str, user_message: str) -> dict:
    """Build an OpenAI-compatible request body for CodeVector."""
    body: dict = {
        "model": _normalize_model_id(CODEVECTOR_MODEL),
        "messages": [
            {
                "role": "user",
                "content": f"{system_prompt}\n\n{user_message}",
            },
        ],
    }
    if CODEVECTOR_TEMPERATURE is not None:
        body["temperature"] = CODEVECTOR_TEMPERATURE
    if CODEVECTOR_MAX_TOKENS is not None and CODEVECTOR_MAX_TOKENS > 0:
        body["max_tokens"] = CODEVECTOR_MAX_TOKENS
    return body

def multi_judge_session(logs, problem_statement: str, files_path: str, output_metrics: list[dict], interact_metrics: list[dict]):
    
    output_system_prompt = _build_system_prompt(output_metrics)
    interact_system_prompt = _build_system_prompt(interact_metrics)
    claude_sonnet_model_id = "us.anthropic.claude-sonnet-4-6"
    model_id = "kimi-k2.6"
    kimi_model = OpenAIModel(
                        model_id=model_id,  # The specific model identifier used by your gateway
                        client_args={
                            "base_url": "https://coding-gateway.fissionlabs.com/gateway/openai/v1",  # Your OpenAI-compatible API base URL
                            "api_key": "cvg_6N2f1hPGczWLj9S5_LrHjFH_ONjPR7GF29ZYC_YLcpo",   # Pass a dummy string if no key is needed
                        },
                        params={
                                "temperature": 1,
                                "stream": False
                        }
                )
    mult_eval = MultiEvaluator(logs, files_path, problem_statement, output_system_prompt, interact_system_prompt, kimi_model, kimi_model, kimi_model)
    mult_eval.eval_output()
    mult_eval.eval_turns_human_candidate()
    (output_res, turn_res, final_res) = mult_eval.eval_final()
    
    return (output_res, turn_res, final_res)


def judge_session(session_json: dict, metrics: list[dict]) -> dict:
    """Score a session against a dynamic set of metrics.

    Args:
        session_json: The interview session log.
        metrics: List of metric definitions, each with key, name, and rubric.

    Returns:
        {"parsed": <dict>, "raw": <str>}
    """
    if not metrics:
        raise HTTPException(
            status_code=400,
            detail="At least one metric must be configured to score an interview.",
        )

    system_prompt = _build_system_prompt(metrics)
    user_message = f"Here is the full session log:\n{json.dumps(session_json, indent=2)}"

    try:
        url = (
            f"{CODEVECTOR_BASE_URL.rstrip('/')}/chat/completions"
            if CODEVECTOR_BASE_URL
            else "https://api.openai.com/v1/chat/completions"
        )
        request_body = _build_request_body(system_prompt, user_message)
        headers = {
            "Authorization": f"Bearer {CODEVECTOR_API_KEY}",
            "Content-Type": "application/json",
            "x-client-app": "opencode",
        }
        response = httpx.post(
            url,
            headers=headers,
            json=request_body,
            timeout=CODEVECTOR_TIMEOUT_SECONDS,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "CodeVector returned %s. Request body: %s. Response: %s",
                exc.response.status_code,
                json.dumps(request_body),
                exc.response.text,
            )
            raise
        data = response.json()
        message = data["choices"][0]["message"]
        raw = message.get("content")
        if raw is None and message.get("reasoning_content"):
            raw = _extract_json_from_reasoning(message["reasoning_content"])
        if raw is None:
            raise HTTPException(
                status_code=502,
                detail="CodeVector returned empty content and no parseable reasoning.",
            )

        cleaned = _strip_markdown_fences(raw)
        return {"parsed": json.loads(cleaned), "raw": raw}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM scoring failed: {exc}",
        ) from exc
