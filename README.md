# Agentic Grounded Newsletter Bot

A small Python service that produces a daily two-section newsletter using
**Google Gemini with Search grounding** and delivers it to a **Telegram** chat.
Designed to run unattended on **EC2 + cron** or **AWS Lambda + EventBridge**, with
process-level retry (shell on EC2, `lambda_handler` on Lambda), idempotency, and
reboot-safe behavior where applicable.

---

## What it does

On each run, `main.py`:

1. **Loads config** from `.env` (`config.py`).
2. **Idempotency guard** — if the last-success timestamp file shows a send within
   the configured window (default **20 hours**), exit `0` immediately. Path:
   `newsletter_bot/logs/last_sent.txt` on EC2/local; `/tmp/newsletter_bot/last_sent.txt`
   on Lambda. This
   stops `@reboot` cron, manual reruns, and bash-level retries from posting the
   same digest twice the same day.
3. **Gemini call 1 — Global news**: prompt asks for top ~12 headlines from the
   last 24h. Tool: `google_search` grounding. Output normalized to `- ` bullets
   (`formatter.normalize_newsletter`). On failure: CloudWatch log, exit `1` (no
   partial send).
4. **Sleeps 75 seconds** to stay under Gemini per-minute rate limits.
5. **Gemini call 2 — US market movers**: top ~20 stocks with `$TICKER`, `%`
   move (when available), and catalyst (when available). If this call **fails**,
   delivers a **partial** newsletter (section 1 + omission note), sends via
   Telegram (up to 2 parts), writes `last_sent.txt`, and exits `0` — counts as
   a successful idempotency run (steps 6–8 skipped on this path).
6. **Validates** the composed message (`validator.validate_newsletter`):
   exact headers `SECTION 1: Global News Brief` / `SECTION 2: Markets & Stocks Brief`,
   section 1 bullets **5–12**, section 2 **5–15**, each section 2 bullet needs a
   ticker (`$TICKER` or `(TICKER)`) and `%`. Failures are **logged** but do not block
   sending on the full-newsletter path.
7. **Splits** if the message exceeds Telegram's 4096-char limit, preferring the
   section boundary; max **2** parts (`formatter.split_for_telegram`).
8. **Sends** via the Telegram Bot API. On send failure, logs error and exits `1`.
9. **Marks success** by writing the current UTC ISO timestamp to
   `logs/last_sent.txt`.

**Hard caps per run:** 2 Gemini calls; up to **2 Telegram newsletter parts** (split
at section boundary).

---

## Repository layout

```
.
├── README.md
├── LICENSE
├── .gitignore             # secrets, logs, venv, .cursor/, etc.
├── build/                 # ephemeral Lambda zip (gitignored; see Lambda section)
├── terraform/             # optional: Lambda + EventBridge (see terraform/README.md)
└── newsletter_bot/
    ├── main.py              # orchestration, idempotency, prompt → Gemini → Telegram
    ├── config.py            # .env loader, AppConfig dataclass
    ├── telegram_client.py   # Bot API client with retry on 5xx/429
    ├── formatter.py         # bullet normalization, 4096-char split
    ├── validator.py         # structure & content checks
    ├── logger.py            # file logger → logs/run.log
    ├── run_with_retry.sh    # shell wrapper: 3 attempts w/ 15m backoff (EC2)
    ├── requirements.txt
    ├── .env.example
    └── logs/                # run.log + last_sent.txt (gitignored)
```

---

## Retry & idempotency (yes, both exist)

| Layer | Mechanism | Where |
|-------|-----------|-------|
| **HTTP-level (Telegram)** | 2 attempts on 5xx / 429; honors `retry_after` from `parameters.retry_after` in the body | `telegram_client.py` |
| **Process-level (whole run, EC2)** | **3 attempts** (1 + 2 retries), **15 minutes** between failures | `run_with_retry.sh` |
| **Process-level (whole run, Lambda)** | Default **3 attempts** (1 + **2 retries**) within a single invocation, spaced as close to **15 minutes** apart as remaining execution time allows (adaptive — see caveat below). `LAMBDA_MAX_ATTEMPTS=1` disables retry. | `main.py::lambda_handler` |
| **Idempotency** | Skip if `logs/last_sent.txt` is within the last `IDEMPOTENCY_WINDOW_HOURS` (default **20h**) | `main.py::should_skip_duplicate` |
| **Failure logging** | Gemini / send failures logged to CloudWatch only; no Telegram failure alerts | `main.py` |

Notes:
- The 20h window is **less than the 24h cron cadence**, so the next scheduled
  daily run is never suppressed. Tune via `IDEMPOTENCY_WINDOW_HOURS` env var.
- Gemini calls are **not** retried individually inside `main.py`. **Call 1** failure
  exits non-zero; **call 2** failure may exit `0` after partial delivery (see step 5).
  **EC2** `run_with_retry.sh` or **Lambda** `lambda_handler` retries the **whole**
  process on non-zero exit. (`validator.build_refined_prompt`
  exists for future use but is not currently wired in.)
- **Lambda caveat (adaptive retry gap):** Lambda's **900 s hard timeout** cannot fit 2 full **15-minute** waits plus 3 runs in one invocation (15m + 15m alone exceeds the entire budget). Instead of skipping retries outright, `lambda_handler` shrinks each wait to whatever time remains (reserving `LAMBDA_POST_SLEEP_RESERVE_MS` for the next `main()` call), so `LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES` is a **ceiling, not a guarantee**, on Lambda. This is what makes `Lambda run X/Y` in logs actually progress (1/3 → 2/3 → 3/3) instead of getting stuck at 1/Y — failures typically return fast (seconds), leaving plenty of runway for real retries even though each gap ends up shorter than 15 minutes. Retries only stop early if under 5s of safe runway remain. **EC2** `run_with_retry.sh` has no such cap and keeps the full 15-minute gaps.
- **Multi-part Telegram send:** if part 1 succeeds and a later part fails, `main.py`
  still writes `last_sent.txt` so a process-level retry does not re-send part 1.
  Tradeoff: outer retry may skip until the idempotency window expires; delete
  `last_sent.txt` or wait out the window to force a full resend.

---

## Timezone dependency

The bot itself uses **UTC internally** (timestamps in `last_sent.txt` and
logs). However, the **schedule** is timezone-sensitive — make sure cron and
the host clock are aligned.

- The README's example cron line is `0 16 * * *` which is **16:00 UTC** (same as
  Terraform default). EventBridge rules are UTC-only.
  - `16:00 UTC` ≈ `11:00 EST` (UTC−5, standard time, winter)
  - `16:00 UTC` ≈ `12:00 EDT` (UTC−4, daylight time, summer)
  - During DST switches, the local-clock send time drifts by 1 hour. If you
    want a stable local-clock time, either:
    1. Set the EC2 system timezone to your local zone (`sudo timedatectl
       set-timezone America/New_York`) and use `0 11 * * *` in cron, or
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
| `GEMINI_MODEL` | No | Defaults to `gemini-2.5-flash-lite`. |
| `REQUEST_TIMEOUT_SECONDS` | No | Telegram HTTP timeout. Default `20`. |
| `IDEMPOTENCY_WINDOW_HOURS` | No | Skip-duplicate window. Default `20`. |
| `LAMBDA_MAX_ATTEMPTS` | No | Lambda only. Default `3` (= 1 run + 2 retries). Use `1` for no process-level retry. |
| `LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES` | No | Lambda only. Default `15`. **Ceiling** on the gap between retries — shrunk to fit remaining execution time within the 900s Lambda timeout (see **Retry & idempotency** caveat). |
| `LAMBDA_POST_SLEEP_RESERVE_MS` | No | Lambda only. Milliseconds reserved after the scheduled sleep for the next full `main()`. Default `240000` (~4m); raise if time guard skips retry too often. |

Secrets: **`.env`** on EC2/local (gitignored). On **Lambda**, set the same keys as **function environment variables** (console or IaC).

---

## Lambda (minimal, no VPC)

Default Lambda has **outbound internet** for Gemini + Telegram. **Do not** attach a VPC unless you add a NAT — VPC without NAT blocks public API calls.

1. **Build zip (Linux, same arch as function: x86_64 vs arm64)** — from repo root:

   ```bash
   mkdir -p build/lambda && rm -rf build/lambda/*
   docker run --rm \
     -v "$(pwd)/newsletter_bot:/src:ro" \
     -v "$(pwd)/build/lambda:/out" \
     public.ecr.aws/lambda/python:3.12 \
     bash -c 'pip install -r /src/requirements.txt -t /out && cp /src/*.py /out/'
   (cd build/lambda && zip -r ../newsletter-lambda.zip .)
   ```

   Upload `build/newsletter-lambda.zip` to the function.

2. Create function: **Python 3.12**, handler **`main.lambda_handler`**, upload zip, **timeout 900 s** (AWS max = 15m; in-handler **3×15m retries do not fit**—see **Retry & idempotency**; EC2 or EventBridge for full retry cadence), memory **512 MB** (tune down if you want).

3. **Configuration → Environment variables**: `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, optional `GEMINI_MODEL`, `IDEMPOTENCY_WINDOW_HOURS`, `REQUEST_TIMEOUT_SECONDS`, and Lambda retry tuning (`LAMBDA_MAX_ATTEMPTS`, `LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES`, `LAMBDA_POST_SLEEP_RESERVE_MS` — see **Configuration**).

4. **EventBridge (or EventBridge Scheduler):** rule with schedule (e.g. `cron(0 16 * * ? *)` for 16:00 UTC), target = this Lambda. Optional: on the target, set **retry attempts = 0** if you do not want AWS to re-invoke after errors (can mean duplicate sends depending on failure timing). **IaC:** see [`terraform/`](terraform/) and [`terraform/README.md`](terraform/README.md) (Terraform default schedule is also 16:00 UTC).

**Note:** Idempotency uses **`/tmp/newsletter_bot`** on Lambda—writable there; state **persists across warm invocations** and is **lost on cold start** (not “wiped after every execution”). One schedule per day + no concurrent invocations is usually enough; for stronger guarantees you’d add S3/DynamoDB (not included here).

**IAM:** Lambda needs **no SES**; outbound HTTPS to Gemini + Telegram only. Attach **AWSLambdaBasicExecutionRole** (CloudWatch Logs) unless you add VPC/NAT yourself.

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
# run_with_retry.sh uses `bc` for retry delay labels (install: sudo apt install bc)

# 4. Smoke test
python main.py && tail -n 50 logs/run.log
```

---

## Persistent cron (survives reboots)

Cron jobs already survive EC2 stop/start because crontabs are stored on disk.
What we add below is **automatic recovery on reboot** (e.g. after an OS patch
or instance restart) without risking duplicate sends.

Edit the path in `run_with_retry.sh` (`cd /home/ubuntu/newsletter_bot`) if your
install location differs, then:

```bash
crontab -e
```

Append both lines:

```cron
# Daily run at 16:00 UTC (~11:00 EST / 12:00 EDT)
0 16 * * * /home/ubuntu/newsletter_bot/run_with_retry.sh >> /home/ubuntu/newsletter_bot/logs/run.log 2>&1

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
- If the daily run was missed entirely (instance down at 16:00 UTC), the
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

- **EC2 / local:** `logs/run.log` — append-only, structured `key=value` lines.
  `logs/last_sent.txt` — single ISO-8601 UTC timestamp of last successful send.
- **Lambda:** logs go to **CloudWatch** (stdout via `logger.py`); idempotency state
  is `/tmp/newsletter_bot/last_sent.txt` (warm invocations only).
- Delete `last_sent.txt` to force the next run to fire regardless of the window.

---

## Troubleshooting

| Symptom | Where to look |
|---------|---------------|
| Exit code 1 | EC2/local: `logs/run.log`. Lambda: CloudWatch log group. Last entries — Gemini call 1 error, Telegram send error, or formatting error. |
| Telegram shows `400` / parse errors | Messages use `parse_mode=Markdown`; odd `$` or `_` in bullets can break parsing. See `telegram_client.py`. |
| No Telegram message arrived | Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; message your bot once from the target chat so the bot can reach it. |
| Validation warnings only | Bullet count or ticker/`%` rule failed; the digest is still sent. Tune the prompts in `main.py` if you want stricter output. |
| Section 2 missing / “Markets section omitted” note | Second Gemini call failed; partial delivery by design (section 1 still sent). |
| Run fired at the wrong wall-clock time | See **Timezone dependency** above; check `timedatectl`. |
| `@reboot` job re-sent the digest | `IDEMPOTENCY_WINDOW_HOURS` is too low or `logs/last_sent.txt` was wiped; defaults should prevent this. |

---

## License

See [LICENSE](LICENSE) in the repository root (MIT).
