variable "aws_region" {
  type        = string
  description = "AWS region for Lambda and EventBridge."
  default     = "us-east-1"
}

variable "schedule_expression" {
  type        = string
  description = "EventBridge schedule (rate or cron). Default: daily 16:00 UTC = 11:00 America/Chicago during CDT (UTC-5). Winter CST: same cron fires 10:00 local. EventBridge is UTC-only."
  default     = "cron(0 16 * * ? *)"
}

variable "lambda_zip_path" {
  type        = string
  description = "Path to deployment zip (build locally first; see README)."
}

variable "function_name" {
  type        = string
  description = "Lambda function name."
  default     = "newsletter-bot"
}

variable "lambda_timeout_seconds" {
  type        = number
  description = "Lambda timeout (default 900s = 15m max). Handler: default 1 run + 2 retries at +15m/+30m from first failure; plus 75s inter-Gemini sleep inside main(). Remaining-time guard may skip retries if next run would not fit. Tune env vars if runs are slower."
  default     = 900
}

variable "lambda_memory_mb" {
  type        = number
  default     = 512
}

variable "gemini_api_key" {
  type        = string
  sensitive   = true
  description = "GEMINI_API_KEY"
}

variable "telegram_bot_token" {
  type        = string
  sensitive   = true
  description = "TELEGRAM_BOT_TOKEN"
}

variable "telegram_chat_id" {
  type        = string
  description = "TELEGRAM_CHAT_ID"
}

variable "gemini_model" {
  type        = string
  default     = ""
  description = "Optional GEMINI_MODEL (empty = use app default)."
}

variable "idempotency_window_hours" {
  type        = string
  default     = ""
  description = "Optional IDEMPOTENCY_WINDOW_HOURS (empty = app default)."
}

variable "request_timeout_seconds" {
  type        = string
  default     = ""
  description = "Optional REQUEST_TIMEOUT_SECONDS (empty = app default)."
}

variable "lambda_max_attempts" {
  type        = string
  default     = ""
  description = "Optional LAMBDA_MAX_ATTEMPTS (empty = app default 3 = 1 + 2 retries; use 1 for no retry)."
}

variable "lambda_retry_after_first_fail_minutes" {
  type        = string
  default     = ""
  description = "Optional LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES (empty = app default 15)."
}

variable "lambda_post_sleep_reserve_ms" {
  type        = string
  default     = ""
  description = "Optional LAMBDA_POST_SLEEP_RESERVE_MS (empty = app default)."
}

