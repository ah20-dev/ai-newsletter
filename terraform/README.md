# Terraform: newsletter Lambda + EventBridge

One-shot deploy: IAM role (basic execution only), Lambda (Python 3.12, `main.lambda_handler`), scheduled invoke.

## Build zip first

From repo root (see root `README.md` Lambda section): produce `build/newsletter-lambda.zip` (Linux-compatible deps + `*.py` at zip root). Terraform uses `filename` + `source_code_hash`; it does **not** build the zip.

## Apply

```bash
cd terraform
terraform init
terraform apply \
  -var="lambda_zip_path=$(pwd)/../build/newsletter-lambda.zip" \
  -var="gemini_api_key=..." \
  -var="telegram_bot_token=..." \
  -var="telegram_chat_id=..." \
```

Default schedule: daily `cron(0 16 * * ? *)` (16:00 UTC ≈ 11:00 AM **CDT**). Override: `-var='schedule_expression=...'`. Also: `-var='aws_region=us-east-1'`, `-var='function_name=newsletter-bot'`, `-var='gemini_model=...'`, `-var='lambda_max_attempts=2'`, `-var='lambda_retry_after_first_fail_minutes=8'`, `-var='lambda_post_sleep_reserve_ms=240000'`, etc.

No VPC, SES, DynamoDB, or explicit log groups—CloudWatch Logs created implicitly by basic execution role.
