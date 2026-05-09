import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google import genai
from google.genai import types

from config import load_config
from formatter import normalize_newsletter, split_for_telegram
from logger import get_logger, log_event, utc_now_iso
from telegram_client import TelegramClient
from validator import SECTION_1_HEADER, SECTION_2_HEADER, validate_newsletter


BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LAST_SENT_FILE = LOG_DIR / "last_sent.txt"

DELAY_BETWEEN_CALLS_SECONDS = 75

# Skip if a successful run is recorded within this many hours. Protects against
# duplicate sends when cron `@reboot` (or a manual rerun) fires shortly after
# the daily run already succeeded. Default 20h: shorter than the 24h cadence so
# the next scheduled run is never blocked, longer than any plausible reboot
# delay so we never resend the same digest.
IDEMPOTENCY_WINDOW_HOURS = int(os.getenv("IDEMPOTENCY_WINDOW_HOURS", "20"))


def build_global_news_prompt() -> str:
    return (
        "What are the top 12 headlines in global news trending in the last 24 hours? "
        "Be concise. Return bullet points only: each line must start with '- '. "
        "No numbering, no extra text or commentary."
    )


def build_stock_movers_prompt() -> str:
    return (
        "What are the top 20 stocks moving in USA news trending in the last 24 hours? "
        "Provide swing % for each when possible. Each bullet must include $TICKER, % move optional depending on data, and the catalyst is highly preferred. "
        "Return bullet points only: each line must start with '- '. No numbering, no extra text."
    )


def should_skip_duplicate() -> bool:
    if not LAST_SENT_FILE.exists():
        return False

    try:
        raw = LAST_SENT_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return False
        last_sent = datetime.fromisoformat(raw)
        now_utc = datetime.now(timezone.utc)
        return now_utc - last_sent <= timedelta(hours=IDEMPOTENCY_WINDOW_HOURS)
    except Exception:
        return False


def mark_sent_now() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LAST_SENT_FILE.write_text(utc_now_iso(), encoding="utf-8")


def generate_with_grounding(
    client: genai.Client, model: str, prompt: str, max_output_tokens: int = 1800
) -> str:
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2,
        max_output_tokens=max_output_tokens,
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )
    return (response.text or "").strip()


def main() -> int:
    start = time.time()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = get_logger(LOG_DIR)

    try:
        cfg = load_config()
    except Exception as exc:
        log_event(logger, "error", "Configuration load failed", error=str(exc))
        return 1

    if should_skip_duplicate():
        log_event(
            logger,
            "info",
            "Idempotency skip",
            reason="already_sent_within_window",
            window_hours=IDEMPOTENCY_WINDOW_HOURS,
        )
        return 0

    telegram = TelegramClient(cfg.telegram_bot_token, timeout_seconds=cfg.request_timeout_seconds)
    client = genai.Client(api_key=cfg.gemini_api_key)

    # Call 1: global news
    try:
        raw_news = generate_with_grounding(
            client, cfg.gemini_model, build_global_news_prompt(), max_output_tokens=512
        )
    except Exception as exc:
        log_event(logger, "error", "Gemini call 1 (news) failed", error=str(exc))
        admin_result = telegram.send_message(
            cfg.admin_chat_id,
            "Newsletter generation failed: first Gemini call failed. Check logs on EC2.",
        )
        log_event(
            logger,
            "error",
            "Admin notified",
            admin_notify_ok=admin_result.ok,
            admin_status=admin_result.status_code,
        )
        return 1

    news_bullets = normalize_newsletter(raw_news)

    log_event(logger, "info", "Waiting 75s before second Gemini call to avoid rate limits")
    time.sleep(DELAY_BETWEEN_CALLS_SECONDS)

    # Call 2: stock movers
    try:
        raw_stocks = generate_with_grounding(
            client, cfg.gemini_model, build_stock_movers_prompt(), max_output_tokens=700
        )
    except Exception as exc:
        log_event(logger, "error", "Gemini call 2 (stocks) failed", error=str(exc))
        admin_result = telegram.send_message(
            cfg.admin_chat_id,
            "Newsletter generation failed: second Gemini call failed. Check logs on EC2.",
        )
        log_event(
            logger,
            "error",
            "Admin notified",
            admin_notify_ok=admin_result.ok,
            admin_status=admin_result.status_code,
        )
        return 1

    stocks_bullets = normalize_newsletter(raw_stocks)

    final_text = (
        SECTION_1_HEADER + "\n\n" + news_bullets + "\n\n" + SECTION_2_HEADER + "\n\n" + stocks_bullets
    )

    validation = validate_newsletter(final_text)
    if not validation.is_valid:
        log_event(
            logger,
            "warning",
            "Validation issues on composed message; sending anyway",
            errors="; ".join(validation.errors),
        )

    try:
        message_parts = split_for_telegram(final_text, max_parts=2)
    except ValueError as exc:
        admin_result = telegram.send_message(
            cfg.admin_chat_id,
            "Newsletter formatting failed: content exceeded Telegram constraints.",
        )
        log_event(
            logger,
            "error",
            "Formatting failure",
            error=str(exc),
            admin_notify_ok=admin_result.ok,
            admin_status=admin_result.status_code,
        )
        return 1

    for idx, part in enumerate(message_parts, start=1):
        send_result = telegram.send_message(cfg.telegram_chat_id, part)
        log_event(
            logger,
            "info" if send_result.ok else "error",
            "Telegram send result",
            part=idx,
            total_parts=len(message_parts),
            status=send_result.status_code,
            ok=send_result.ok,
            description=send_result.description,
        )
        if not send_result.ok:
            admin_result = telegram.send_message(
                cfg.admin_chat_id,
                f"Newsletter send failed on part {idx}/{len(message_parts)}. Check logs.",
            )
            log_event(
                logger,
                "error",
                "Admin alert sent for Telegram failure",
                admin_notify_ok=admin_result.ok,
                admin_status=admin_result.status_code,
            )
            return 1

    mark_sent_now()

    duration_seconds = round(time.time() - start, 2)
    section1_count = validation.section1_count if validation else 0
    section2_count = validation.section2_count if validation else 0
    log_event(
        logger,
        "info",
        "Run completed",
        timestamp_utc=utc_now_iso(),
        duration_seconds=duration_seconds,
        gemini_calls=2,
        section1_bullets=section1_count,
        section2_bullets=section2_count,
        telegram_parts_sent=len(message_parts),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
