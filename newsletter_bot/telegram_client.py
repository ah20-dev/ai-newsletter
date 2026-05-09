import time
from dataclasses import dataclass

import requests


@dataclass
class TelegramSendResult:
    ok: bool
    status_code: int
    description: str


class TelegramClient:
    def __init__(self, bot_token: str, timeout_seconds: int = 20) -> None:
        self.bot_token = bot_token
        self.timeout_seconds = timeout_seconds
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> TelegramSendResult:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        attempts = 2
        last_status = 0
        last_description = "Unknown error"

        for attempt in range(attempts):
            try:
                response = requests.post(self.url, json=payload, timeout=self.timeout_seconds)
                last_status = response.status_code
                body = response.json() if response.content else {}

                if response.status_code == 200 and body.get("ok") is True:
                    return TelegramSendResult(True, response.status_code, "sent")

                retry_after = 0
                params = body.get("parameters") if isinstance(body, dict) else None
                if isinstance(params, dict):
                    retry_after = int(params.get("retry_after", 0))

                last_description = body.get("description", f"HTTP {response.status_code}")
                is_retryable = response.status_code >= 500 or response.status_code == 429
                if attempt < attempts - 1 and is_retryable:
                    time.sleep(max(retry_after, 1))
                    continue
                return TelegramSendResult(False, response.status_code, last_description)
            except requests.RequestException as exc:
                last_description = str(exc)
                if attempt < attempts - 1:
                    time.sleep(1)
                    continue
                return TelegramSendResult(False, last_status, last_description)

        return TelegramSendResult(False, last_status, last_description)
