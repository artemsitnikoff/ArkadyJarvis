#!/usr/bin/env python3
"""Quick sanity check for the LLM rejection classifier.

Prints score/reasoning/verdict for a built-in list of replies.
Add custom replies as CLI args:
    docker compose exec bot python scripts/test_rejection_classifier.py "уже не актуально" "когда удобно созвониться?"
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

from app.config import settings  # noqa: E402
from app.services.ai_client import AIClient  # noqa: E402
from app.services.rejection_classifier import classify_rejection_intent  # noqa: E402

DEFAULT_TESTS = [
    # Should be reject
    "Добрый день, уже не актуально",
    "Спасибо, я нашёл работу",
    "Не интересно",
    "Я уже принял оффер от другой компании, благодарю",
    "Извините, в данный момент не рассматриваю предложения",
    "Спасибо, эта вакансия мне не подходит",
    "Уже трудоустроен, не ищу",
    "Думаю, мы зря тратим время друг друга — у меня уже всё",
    # Should be NOT reject
    "Здравствуйте! Готов поговорить, когда удобно?",
    "Расскажите чуть больше о компании и задачах",
    "Зарплата какая? Удалёнка возможна?",
    "Привет",
    "Можем созвониться завтра в 14:00?",
    "Да",
    # Ambiguous
    "Сейчас занят, ответ напишу позже",
    "Дайте подумать пару дней",
]


async def main(extra_tests: list[str]) -> None:
    threshold = settings.rejection_classifier_threshold
    print(f"\nThreshold: {threshold}/100  (score > {threshold} → auto-reject)\n")
    print(f"{'SCORE':>5} {'VERDICT':<12} {'TEXT':<70} REASONING")
    print("-" * 130)

    ai = AIClient()
    try:
        tests = DEFAULT_TESTS + (extra_tests or [])
        for text in tests:
            result = await classify_rejection_intent(text, ai)
            score = result["score"]
            reasoning = result["reasoning"]
            verdict = "🚫 REJECT" if score > threshold else ("⚠️  ambiguous" if score >= 31 else "✅ keep")
            t = text if len(text) <= 67 else text[:67] + "…"
            r = reasoning if len(reasoning) <= 80 else reasoning[:80] + "…"
            print(f"{score:>3}/100 {verdict:<12} {t:<70} {r}")
    finally:
        await ai.close()


asyncio.run(main(sys.argv[1:]))
