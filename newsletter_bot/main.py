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
_IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
# Lambda: only /tmp is writable; idempotency file survives warm reuse, not cold starts.
LOG_DIR = Path("/tmp/newsletter_bot") if _IS_LAMBDA else BASE_DIR / "logs"
LAST_SENT_FILE = LOG_DIR / "last_sent.txt"

DELAY_BETWEEN_CALLS_SECONDS = 75

# Skip if a successful run is recorded within this many hours. Protects against
# duplicate sends when cron `@reboot` (or a manual rerun) fires shortly after
# the daily run already succeeded. Default 20h: shorter than the 24h cadence so
# the next scheduled run is never blocked, longer than any plausible reboot
# delay so we never resend the same digest.
IDEMPOTENCY_WINDOW_HOURS = int(os.getenv("IDEMPOTENCY_WINDOW_HOURS", "20"))

# Process-level retries: EC2 uses run_with_retry.sh (3 attempts). Lambda uses lambda_handler only:
# default **1 initial + 2 retries**, **15 minutes** apart (anchor: 15m, 30m from first failure).
LAMBDA_MAX_ATTEMPTS = int(os.getenv("LAMBDA_MAX_ATTEMPTS", "3"))
LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES = int(os.getenv("LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES", "15"))
LAMBDA_POST_SLEEP_RESERVE_MS = int(os.getenv("LAMBDA_POST_SLEEP_RESERVE_MS", "240000"))


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


def _lambda_run_label(lambda_run: int | None, lambda_run_max: int | None) -> str:
    if lambda_run is None or lambda_run_max is None:
        return ""
    return f" [Lambda run {lambda_run}/{lambda_run_max}]"


def _bullet_count(block: str) -> int:
    return sum(1 for line in block.strip().splitlines() if line.strip().startswith("- "))


_PARTIAL_SECTION2_NOTE = "- Note: Markets and stocks section omitted (second Gemini request failed)."


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


def main(*, lambda_run: int | None = None, lambda_run_max: int | None = None) -> int:
    start = time.time()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = get_logger(LOG_DIR)
    run_lbl = _lambda_run_label(lambda_run, lambda_run_max)

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
        _run_ctx = {}
        if lambda_run is not None:
            _run_ctx["lambda_run"] = lambda_run
            _run_ctx["lambda_run_max"] = lambda_run_max
        log_event(
            logger,
            "error",
            "Gemini call 1 (news) failed",
            error=str(exc),
            **_run_ctx,
        )
        alert_send = telegram.send_message(
            cfg.telegram_chat_id,
            f"Newsletter generation failed: first Gemini call failed.{run_lbl}",
        )
        log_event(
            logger,
            "error",
            "Failure notice sent via Telegram",
            alert_send_ok=alert_send.ok,
            alert_send_status=alert_send.status_code,
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
        _run_ctx = {}
        if lambda_run is not None:
            _run_ctx["lambda_run"] = lambda_run
            _run_ctx["lambda_run_max"] = lambda_run_max
        log_event(
            logger,
            "error",
            "Gemini call 2 (stocks) failed; delivering section 1 only",
            error=str(exc),
            **_run_ctx,
        )
        partial_text = SECTION_1_HEADER + "\n\n" + news_bullets + "\n\n" + _PARTIAL_SECTION2_NOTE
        try:
            message_parts = split_for_telegram(partial_text, max_parts=2)
        except ValueError as ferr:
            log_event(logger, "error", "Partial newsletter split failed", error=str(ferr))
            return 1

        for idx, part in enumerate(message_parts, start=1):
            send_result = telegram.send_message(cfg.telegram_chat_id, part)
            log_event(
                logger,
                "info" if send_result.ok else "error",
                "Telegram send result (partial newsletter)",
                part=idx,
                total_parts=len(message_parts),
                status=send_result.status_code,
                ok=send_result.ok,
                description=send_result.description,
                partial_delivery=True,
            )
            if not send_result.ok:
                # Part 1+ already reached Telegram: record idempotency so Lambda retry / next run
                # does not re-send those parts (duplicate). Tradeoff: retry may skip until window
                # expires; fix Telegram / clear last_sent if you must force a full resend.
                if idx > 1:
                    mark_sent_now()
                    log_event(
                        logger,
                        "warning",
                        "Partial newsletter: recorded send after part success (mid-stream failure)",
                        parts_ok_before_fail=idx - 1,
                        total_parts=len(message_parts),
                        partial_delivery=True,
                    )
                alt = telegram.send_message(
                    cfg.telegram_chat_id,
                    f"Partial newsletter send failed on part {idx}/{len(message_parts)}.{run_lbl}",
                )
                log_event(
                    logger,
                    "error",
                    "Failure notice sent via Telegram (partial send)",
                    alert_send_ok=alt.ok,
                    alert_send_status=alt.status_code,
                )
                return 1

        mark_sent_now()
        duration_seconds = round(time.time() - start, 2)
        _done_ctx = {**_run_ctx}
        log_event(
            logger,
            "warning",
            "Run completed with partial newsletter (section 2 omitted)",
            timestamp_utc=utc_now_iso(),
            duration_seconds=duration_seconds,
            gemini_calls=2,
            partial_delivery=True,
            section1_bullets=_bullet_count(news_bullets),
            section2_bullets=0,
            telegram_parts_sent=len(message_parts),
            **_done_ctx,
        )
        return 0

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
        alert_send = telegram.send_message(
            cfg.telegram_chat_id,
            f"Newsletter formatting failed: content exceeded Telegram constraints.{run_lbl}",
        )
        log_event(
            logger,
            "error",
            "Formatting failure",
            error=str(exc),
            alert_send_ok=alert_send.ok,
            alert_send_status=alert_send.status_code,
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
            # At least one prior part reached Telegram: record so retry / later run does not
            # re-send section 1 (idempotency). Tradeoff: Lambda retry may skip until window expires.
            if idx > 1:
                mark_sent_now()
                log_event(
                    logger,
                    "warning",
                    "Newsletter: recorded send after part success (mid-stream Telegram failure)",
                    parts_ok_before_fail=idx - 1,
                    total_parts=len(message_parts),
                )
            alert_send = telegram.send_message(
                cfg.telegram_chat_id,
                f"Newsletter send failed on part {idx}/{len(message_parts)}.{run_lbl}",
            )
            log_event(
                logger,
                "error",
                "Failure notice sent via Telegram",
                alert_send_ok=alert_send.ok,
                alert_send_status=alert_send.status_code,
            )
            return 1

    mark_sent_now()

    duration_seconds = round(time.time() - start, 2)
    section1_count = validation.section1_count
    section2_count = validation.section2_count
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


def lambda_handler(event: object, context: object) -> dict[str, object]:
    """EventBridge / console invoke. Lambda: default **3** full runs (1 + 2 retries),
    all within this single invocation.

    After each failed ``main()``, retries as close to ``LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES``
    apart as the invocation's remaining time allows: Lambda's own hard timeout (max 900s / 15m)
    cannot fit a strict 15m-then-30m schedule for 3 attempts, so the wait is capped (never
    skipped outright while any reasonable time remains) to keep ``attempt`` progressing —
    this is what makes ``Lambda run X/Y`` in logs/alerts increment correctly instead of
    getting stuck at 1/Y. Only bails without retrying if under 5s of safe runway remain.

    No VPC → default outbound internet for Gemini + Telegram.
    """
    logger = get_logger(LOG_DIR)

    if LAMBDA_MAX_ATTEMPTS < 1:
        raise ValueError(f"LAMBDA_MAX_ATTEMPTS must be >= 1; got {LAMBDA_MAX_ATTEMPTS}.")

    configured_gap_seconds = LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES * 60
    reserve_seconds = LAMBDA_POST_SLEEP_RESERVE_MS / 1000.0

    for attempt in range(1, LAMBDA_MAX_ATTEMPTS + 1):
        code = main(lambda_run=attempt, lambda_run_max=LAMBDA_MAX_ATTEMPTS)
        if code == 0:
            log_event(logger, "info", "Lambda handler success", attempt=attempt)
            return {"ok": True, "exit": 0, "attempt": attempt}

        if attempt >= LAMBDA_MAX_ATTEMPTS:
            log_event(
                logger,
                "error",
                "Lambda handler exhausted attempts",
                attempt=attempt,
                exit=code,
            )
            return {"ok": False, "exit": code, "attempt": attempt}

        remaining_ms: int | None = None
        if context is not None and callable(getattr(context, "get_remaining_time_in_ms", None)):
            remaining_ms = int(context.get_remaining_time_in_ms())

        if remaining_ms is None:
            # No Lambda context (e.g. local invocation): honor the configured gap as-is.
            delay = float(configured_gap_seconds)
        else:
            available_seconds = (remaining_ms / 1000.0) - reserve_seconds
            if available_seconds <= 5:
                log_event(
                    logger,
                    "warning",
                    "Lambda handler skipping further retries (no time left this invocation)",
                    attempt=attempt,
                    remaining_ms=remaining_ms,
                    exit=code,
                )
                return {
                    "ok": False,
                    "exit": code,
                    "attempt": attempt,
                    "reason": "insufficient_time_for_scheduled_retry",
                }
            delay = min(float(configured_gap_seconds), available_seconds)

        log_event(
            logger,
            "info",
            "Lambda handler retry wait",
            attempt=attempt,
            delay_seconds=round(delay, 2),
            next_attempt=attempt + 1,
            configured_gap_seconds=configured_gap_seconds,
        )
        time.sleep(delay)

    # Defensive: loop should always return (success, exhausted attempts, or time guard).
    log_event(logger, "error", "Lambda handler exited retry loop without return (bug)")
    return {"ok": False, "exit": 1, "reason": "unexpected_handler_exit"}


if __name__ == "__main__":
    sys.exit(main())
