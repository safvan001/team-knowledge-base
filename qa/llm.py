"""Thin wrapper around Gemini.

Isolated in one module for two reasons: the rest of the system can be tested
without a network, and swapping the provider means editing this file only.

Every function here degrades to a safe empty result when no API key is
configured, so the application runs end to end with no credentials.
"""

import json
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

_client = None


def llm_available():
    return bool(settings.GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def _generate(prompt):
    """One call. Returns text, or None if unavailable or failing.

    Uses the Interactions API, which is what Google recommends for new
    development. `store=False` keeps it stateless: each call is independent,
    and the team's internal knowledge is not retained server-side between
    requests. Both matter here - there is no conversation to carry forward,
    and the context being sent is the company's own information.

    Answer generation is a convenience over retrieval, not the source of
    truth, so a provider failure degrades the response rather than breaking
    the request.
    """
    if not llm_available():
        return None
    try:
        interaction = _get_client().interactions.create(
            model=settings.GEMINI_MODEL, input=prompt, store=False
        )
        return (interaction.output_text or "").strip()
    except Exception as exc:
        logger.warning("Gemini call failed, falling back to template: %s", exc)
        return None


def extract_entities(question, names):
    """Which of these known entity names does the question refer to?

    Used only when literal matching found nothing. Returns [] on any failure,
    which sends the caller on to text search.
    """
    catalogue = "\n".join(f"- {n}" for n in names)
    prompt = (
        "You match a question to entities in a knowledge base.\n"
        "Return ONLY a JSON array of names copied exactly from the list.\n"
        "Include a name only if the question refers to it. Return [] if none do.\n\n"
        f"Known entities:\n{catalogue}\n\n"
        f"Question: {question}\n\n"
        "JSON array:"
    )
    raw = _generate(prompt)
    if not raw:
        return []

    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def write_answer(question, context_text):
    """Turn retrieved context into prose. Returns None if unavailable."""
    prompt = (
        "You answer questions for a small AI consulting team using their "
        "knowledge base.\n\n"
        "Rules:\n"
        "- Use ONLY the context below. Never invent names, dates or decisions.\n"
        "- Explain the connections between things, not just isolated facts.\n"
        "- Name the people, decisions and dates involved where the context has them.\n"
        "- If the context does not answer the question, say so plainly.\n"
        "- Three short paragraphs at most. No preamble, no bullet lists.\n\n"
        f"Context:\n{context_text}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )
    return _generate(prompt)
