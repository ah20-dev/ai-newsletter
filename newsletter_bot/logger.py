import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "run.log"

    logger = logging.getLogger("newsletter_bot")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    file_handler = logging.FileHandler(log_path)
    formatter = logging.Formatter(
        "%(asctime)sZ | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log_event(logger: logging.Logger, level: str, message: str, **fields: Any) -> None:
    if fields:
        kv = " ".join(f"{k}={v}" for k, v in fields.items())
        payload = f"{message} | {kv}"
    else:
        payload = message

    if level.lower() == "error":
        logger.error(payload)
    elif level.lower() == "warning":
        logger.warning(payload)
    else:
        logger.info(payload)
