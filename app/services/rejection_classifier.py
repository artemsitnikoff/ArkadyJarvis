"""LLM-based classifier: is the candidate's reply a rejection?

Uses the same Claude CLI as resume scoring. Returns a 0–100 score and
short reasoning. Caller picks a threshold (default 70 — see settings).
"""
import logging

from app.services.ai_client import AIClient
from app.services.prompts import load_prompt
from app.utils import parse_json_response

logger = logging.getLogger("arkadyjarvis")

_PROMPT_TEMPLATE = load_prompt("rejection_classifier")


async def classify_rejection_intent(text: str, ai_client: AIClient) -> dict:
    """Score how likely the reply expresses rejection of the vacancy.

    Returns a dict {"score": int 0-100, "reasoning": str}. On any error
    returns score=0 with the error in reasoning — the caller treats low
    scores as 'not a rejection' so failure is safe (no false-positives).
    """
    if not text or not text.strip():
        return {"score": 0, "reasoning": "пустой текст"}

    prompt = _PROMPT_TEMPLATE.replace("{text}", text)
    try:
        response = await ai_client.complete(prompt, timeout=30)
    except Exception as e:
        logger.warning("Rejection classifier: AI call failed: %s", e)
        return {"score": 0, "reasoning": f"AI error: {e}"}

    try:
        result = parse_json_response(response)
        score = int(result.get("score", 0))
        score = max(0, min(100, score))  # clamp 0..100
        reasoning = str(result.get("reasoning", "")).strip() or "no reasoning"
        return {"score": score, "reasoning": reasoning}
    except Exception as e:
        logger.warning("Rejection classifier: parse failed: %s | raw=%r", e, response[:300])
        return {"score": 0, "reasoning": f"parse error: {e}"}
