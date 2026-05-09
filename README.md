# Agentic Grounded Newsletter Bot

A small Python service that produces a daily two-section newsletter using
**Google Gemini with Search grounding** and delivers it to a **Telegram** chat.
Designed to run unattended on an EC2 instance via `cron`, with retry,
idempotency, and reboot-safe behavior.

---

## What it does

On each run, `main.py`:

1. **Loads config** from `.env` (`config.py`).
2. **Idempotency guard** — if `logs/last_sent.txt` shows a successful send within
   the configured window (default **20 hours**), exit `0` immediately. This
   stops `@reboot` cron, manual reruns, and bash-level retries from posting the
   same digest twice the same day.
3. **Gemini call 1 — Global news**: prompt asks for top ~12 headlines from the
   last 24h. Tool: `google_search` grounding. Output normalized to `- ` bullets
   (`formatter.normalize_newsletter`).
4. **Sleeps 75 seconds** to stay under Gemini per-minute rate limits.
5. **Gemini call 2 — US market movers**: top ~20 stocks with `$TICKER`, `%`
   move (when available), and catalyst (when available).
6. **Validates** the composed message (`validator.validate_newsletter`):
   section headers present and ordered, bullet counts within bounds, section 2
   bullets contain a ticker and `%`. Failures are **logged** but do not block
   sending.
7. **Splits** if the message exceeds Telegram's 4096-char limit, preferring the
   section boundary; max **2** parts (`formatter.split_for_telegram`).
8. **Sends** via the Telegram Bot API. On send failure, posts an alert to
   `ADMIN_CHAT_ID`.
9. **Marks success** by writing the current UTC ISO timestamp to
   `logs/last_sent.txt`.

**Hard caps per run:** 2 Gemini calls, 2 Telegram messages.

---

## Repository layout

```
.
├── README.md
├── LICENSE
├── .gitignore
└── newsletter_bot/
    ├── main.py              # orchestration, idempotency, prompt → Gemini → Telegram
    ├── config.py            # .env loader, AppConfig dataclass
    ├── telegram_client.py   # Bot API client with retry on 5xx/429
    ├── formatter.py         # bullet normalization, 4096-char split
    ├── validator.py         # structure & content checks
    ├── logger.py            # file logger → logs/run.log
    ├── run_with_retry.sh    # shell wrapper: 5 attempts w/ backoff (EC2)
    ├── requirements.txt
    ├── .env.example
    └── logs/                # run.log + last_sent.txt (gitignored)
```

---

## Retry & idempotency (yes, both exist)

| Layer | Mechanism | Where |
|-------|-----------|-------|
| **HTTP-level (Telegram)** | 2 attempts on 5xx / 429; honors `retry_after` from `parameters.retry_after` in the body | `telegram_client.py` |
| **Process-level (whole run)** | 5 attempts, backoffs **3 / 7 / 12 / 15 minutes** between failures | `run_with_retry.sh` |
| **Idempotency** | Skip if `logs/last_sent.txt` is within the last `IDEMPOTENCY_WINDOW_HOURS` (default **20h**) | `main.py::should_skip_duplicate` |
| **Failure alerting** | Each unrecoverable failure posts a short message to `ADMIN_CHAT_ID` | `main.py` |

Notes:
- The 20h window is **less than the 24h cron cadence**, so the next scheduled
  daily run is never suppressed. Tune via `IDEMPOTENCY_WINDOW_HOURS` env var.
- Gemini calls themselves are **not** retried inside `main.py`. A failed call
  exits non-zero and the outer `run_with_retry.sh` will retry the entire
  process. (`validator.build_refined_prompt` exists for future use but is not
  currently wired in.)

---

## Timezone dependency

The bot itself uses **UTC internally** (timestamps in `last_sent.txt` and
logs). However, the **schedule** is timezone-sensitive — make sure cron and
the host clock are aligned.

- The README's example cron line is `0 17 * * *` which is **17:00 UTC**.
  - `17:00 UTC` ≈ `12:00 EST` (UTC−5, standard time, winter)
  - `17:00 UTC` ≈ `13:00 EDT` (UTC−4, daylight time, summer)
  - During DST switches, the local-clock send time drifts by 1 hour. If you
    want a stable local-clock time, either:
    1. Set the EC2 system timezone to your local zone (`sudo timedatectl
       set-timezone America/New_York`) and use `0 12 * * *` in cron, or
    2. Keep UTC system time and adjust the cron hour twice a year.
- Verify the box is on UTC (recommended for EC2): `timedatectl` should show
  `Time zone: Etc/UTC` or `UTC`.
- The 24h-rolling window in the news prompt is enforced **by Gemini**, not by
  the bot, so it follows whatever "now" the model resolves at request time.

---

## Prerequisites

- Python 3.10+
- A Gemini API key (Google AI Studio)
- A Telegram bot token (`@BotFather`) and the numeric chat ID(s) to send to
- Linux host with `cron` for scheduled runs

---

## Local setup (macOS)

```bash
cd newsletter_bot
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env       # then edit .env with your keys
chmod 600 .env
python main.py             # one-shot test
cat logs/run.log
```

Exit code `0` = success; the newsletter should land in your Telegram chat.

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `GEMINI_API_KEY` | Yes | Gemini API key from Google AI Studio. |
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from `@BotFather`. |
| `TELEGRAM_CHAT_ID` | Yes | Chat ID that receives the newsletter. |
| `ADMIN_CHAT_ID` | Yes | Chat ID for failure alerts (can equal `TELEGRAM_CHAT_ID`). |
| `GEMINI_MODEL` | No | Defaults to `gemini-2.5-flash-lite`. |
| `REQUEST_TIMEOUT_SECONDS` | No | Telegram HTTP timeout. Default `20`. |
| `IDEMPOTENCY_WINDOW_HOURS` | No | Skip-duplicate window. Default `20`. |

Secrets live **only** in `.env`. The repo's `.gitignore` excludes `.env*`
(except `.env.example`). On EC2 also `chmod 600 .env`.

---

## Deploy to EC2 (Ubuntu / Amazon Linux)

```bash
# 1. Package locally
tar -czf newsletter_bot.tar.gz newsletter_bot

# 2. Upload
scp -i /path/to/key.pem newsletter_bot.tar.gz ubuntu@<host>:~

# 3. On the instance
ssh -i /path/to/key.pem ubuntu@<host>
tar -xzf newsletter_bot.tar.gz && cd newsletter_bot
python3 -m venv venv && source venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
cp .env.example .env
nano .env                  # paste real keys
chmod 600 .env
chmod +x run_with_retry.sh

# 4. Smoke test
python main.py && tail -n 50 logs/run.log
```

---

## Persistent cron (survives reboots)

Cron jobs already survive EC2 stop/start because crontabs are stored on disk.
What we add below is **automatic recovery on reboot** (e.g. after an OS patch
or instance restart) without risking duplicate sends.

Edit the path in `run_with_retry.sh` (`cd /home/ssm-user/newsletter_bot`) to
match your install location, then:

```bash
crontab -e
```

Append both lines:

```cron
# Daily run at 17:00 UTC (~12:00 EST / 13:00 EDT)
0 17 * * * /home/ubuntu/newsletter_bot/run_with_retry.sh >> /home/ubuntu/newsletter_bot/logs/run.log 2>&1

# Reboot recovery: also try after every boot. The 20h idempotency window in
# main.py prevents duplicate sends if the daily run already succeeded.
@reboot sleep 60 && /home/ubuntu/newsletter_bot/run_with_retry.sh >> /home/ubuntu/newsletter_bot/logs/run.log 2>&1
```

How the reboot path is safe:

- After a successful run, `logs/last_sent.txt` is written with the UTC
  timestamp.
- On reboot, `@reboot` runs the wrapper. `main.py` sees the recent timestamp,
  the elapsed time is `<= IDEMPOTENCY_WINDOW_HOURS`, and it exits early with a
  log line `Idempotency skip | reason=already_sent_within_window`.
- If the daily run was missed entirely (instance down at 17:00 UTC), the
  `@reboot` run will see no recent `last_sent.txt` (or one older than the
  window) and will actually post the digest — recovering the missed day.

Verify:

```bash
crontab -l
sudo systemctl status cron     # Ubuntu
sudo systemctl status crond    # Amazon Linux
```

---

## Logs & runtime state

- `logs/run.log` — append-only, structured `key=value` lines.
- `logs/last_sent.txt` — single ISO-8601 UTC timestamp of last successful send.
  Delete this file to force the next run to fire regardless of the window.

---

## Troubleshooting

| Symptom | Where to look |
|---------|---------------|
| Exit code 1 | `logs/run.log` last entries — Gemini error, Telegram error, or formatting error. Admin chat will also have an alert. |
| No Telegram message arrived | Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; message your bot once from the target chat so the bot can reach it. |
| Validation warnings only | Bullet count or ticker/`%` rule failed; the digest is still sent. Tune the prompts in `main.py` if you want stricter output. |
| Run fired at the wrong wall-clock time | See **Timezone dependency** above; check `timedatectl`. |
| `@reboot` job re-sent the digest | `IDEMPOTENCY_WINDOW_HOURS` is too low or `logs/last_sent.txt` was wiped; defaults should prevent this. |

---

## License

See [LICENSE](LICENSE) in the repository root (MIT).
