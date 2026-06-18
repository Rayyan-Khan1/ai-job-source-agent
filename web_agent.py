import logging
import os
import re

from dotenv import load_dotenv
from google import genai

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (add it to a .env file)")
        _client = genai.Client(api_key=api_key)
    return _client


def pick_link(links: list[dict], goal: str) -> str | None:
    """Ask the AI to pick the link whose URL best matches the goal.

    `links` is a list of {"href": str, "text": str} dicts.
    Returns the chosen href, or None if no link matches.
    """
    if not links:
        return None

    listing = "\n".join(
        f'{i}. text="{link["text"]}" url="{link["href"]}"'
        for i, link in enumerate(links)
    )
    prompt = (
        f"{goal}\n\n"
        f"Links found on the page:\n{listing}\n\n"
        "Reply with only the number of the best matching link. "
        "If none of the links match, reply with -1."
    )

    client = _get_client()
    response = client.models.generate_content(model=MODEL, contents=prompt)
    text = (response.text or "").strip()

    match = re.search(r"-?\d+", text)
    if not match:
        logger.warning("Web agent returned an unparseable response: %r", text)
        return None

    index = int(match.group())
    if 0 <= index < len(links):
        return links[index]["href"]
    return None
