# Terraform: newsletter Lambda + EventBridge

Deploys:

- IAM role `${function_name}-exec` + **AWSLambdaBasicExecutionRole** (CloudWatch Logs only)
- Lambda **Python 3.12**, handler `main.lambda_handler`, zip from `lambda_zip_path`
- EventBridge rule `${function_name}-schedule` → Lambda invoke permission

No VPC, NAT, SES, DynamoDB, or explicit log groups. EventBridge target uses AWS defaults for async retries (not configured in this module).

**Requires:** Terraform `>= 1.0`, AWS provider `~> 5.0`, AWS credentials for `var.aws_region` (default `us-east-1`).

## Build zip first

From repo root (see root [`README.md`](../README.md) Lambda section): produce `build/newsletter-lambda.zip` (Linux-compatible deps + `*.py` at zip root). Terraform uses `filename` + `source_code_hash`; it does **not** build the zip. Re-run `terraform apply` after rebuilding the zip so the function updates.

## Apply

Required variables (no defaults): `lambda_zip_path`, `gemini_api_key`, `telegram_bot_token`, `telegram_chat_id`.

```bash
cd terraform
terraform init
terraform apply \
  -var="lambda_zip_path=$(pwd)/../build/newsletter-lambda.zip" \
  -var="gemini_api_key=..." \
  -var="telegram_bot_token=..." \
  -var="telegram_chat_id=..."
```

Prefer secrets via env (not shell history): `export TF_VAR_gemini_api_key=...` (same for `telegram_bot_token`, `telegram_chat_id`), then omit those `-var` flags.

### Schedule (default)

`cron(0 16 * * ? *)` — **16:00 UTC** daily. EventBridge is UTC-only; local wall clock drifts with DST:

- America/Chicago: ≈ **11:00 CDT**, ≈ **10:00 CST**
- US Eastern: ≈ **11:00 EST**, ≈ **12:00 EDT**

Override: `-var='schedule_expression=cron(0 16 * * ? *)'`

### Optional variables

| Terraform variable | Lambda env (when set) | Default in app |
|--------------------|------------------------|----------------|
| `aws_region` | — (provider region) | `us-east-1` |
| `function_name` | — | `newsletter-bot` |
| `schedule_expression` | — | `cron(0 16 * * ? *)` |
| `lambda_timeout_seconds` | — | `900` |
| `lambda_memory_mb` | — | `512` |
| `gemini_model` | `GEMINI_MODEL` | `gemini-2.5-flash-lite` |
| `idempotency_window_hours` | `IDEMPOTENCY_WINDOW_HOURS` | `20` |
| `request_timeout_seconds` | `REQUEST_TIMEOUT_SECONDS` | `20` |
| `lambda_max_attempts` | `LAMBDA_MAX_ATTEMPTS` | `3` |
| `lambda_retry_after_first_fail_minutes` | `LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES` | `15` |
| `lambda_post_sleep_reserve_ms` | `LAMBDA_POST_SLEEP_RESERVE_MS` | `240000` |

Empty optional strings are omitted from the function environment (app defaults apply).

Example with tuning:

```bash
terraform apply \
  -var="lambda_zip_path=$(pwd)/../build/newsletter-lambda.zip" \
  -var="gemini_api_key=$GEMINI_API_KEY" \
  -var="telegram_bot_token=$TELEGRAM_BOT_TOKEN" \
  -var="telegram_chat_id=$TELEGRAM_CHAT_ID" \
  -var='idempotency_window_hours=20' \
  -var='lambda_max_attempts=3' \
  -var='lambda_retry_after_first_fail_minutes=15' \
  -var='lambda_post_sleep_reserve_ms=240000'
```

## Outputs

After apply:

| Output | Description |
|--------|-------------|
| `lambda_function_name` | Lambda name |
| `lambda_function_arn` | Lambda ARN |
| `event_rule_name` | EventBridge rule name |

```bash
terraform output
```

## Destroy

```bash
cd terraform
terraform destroy
```

(Same required/sensitive vars as apply if Terraform prompts for them.)
