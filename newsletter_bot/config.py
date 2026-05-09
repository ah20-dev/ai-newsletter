import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv


RSS_FEEDS: Dict[str, List[str]] = {
    "Technology": [
        "https://techcrunch.com/feed/",
        "https://www.wired.com/feed/rss",
    ],
    "Business": [
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
    ],
    "Science": [
        "https://www.sciencedaily.com/rss/top/science.xml",
        "http://rss.sciam.com/ScientificAmerican-Global",
    ],
    "Politics": [
        "https://feeds.npr.org/1014/rss.xml",
    ],
    "Sports": [
        "https://www.espn.com/espn/rss/news",
    ],
    "World": [
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.npr.org/1004/rss.xml",
    ],
    "Finance": [
        "https://fortune.com/feed/fortune-feeds/?id=3230629",
        "https://seekingalpha.com/feed.xml",
    ],
}


@dataclass(frozen=True)
class AppConfig:
    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    admin_chat_id: str
    gemini_model: str
    request_timeout_seconds: int


def load_config() -> AppConfig:
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

    required = [
        "GEMINI_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ADMIN_CHAT_ID",
    ]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    return AppConfig(
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        admin_chat_id=os.environ["ADMIN_CHAT_ID"],
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
    )


def build_suggested_sources_text() -> str:
    lines: List[str] = []
    for category, urls in RSS_FEEDS.items():
        joined = ", ".join(urls)
        lines.append(f"- {category}: {joined}")
    return "\n".join(lines)
