data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.function_name}-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

locals {
  env_gemini_model         = trimspace(var.gemini_model) != "" ? { GEMINI_MODEL = var.gemini_model } : {}
  env_idempotency_window   = trimspace(var.idempotency_window_hours) != "" ? { IDEMPOTENCY_WINDOW_HOURS = var.idempotency_window_hours } : {}
  env_request_timeout      = trimspace(var.request_timeout_seconds) != "" ? { REQUEST_TIMEOUT_SECONDS = var.request_timeout_seconds } : {}
  env_lambda_max_attempts  = trimspace(var.lambda_max_attempts) != "" ? { LAMBDA_MAX_ATTEMPTS = var.lambda_max_attempts } : {}
  env_lambda_retry_m1      = trimspace(var.lambda_retry_after_first_fail_minutes) != "" ? { LAMBDA_RETRY_AFTER_FIRST_FAIL_MINUTES = var.lambda_retry_after_first_fail_minutes } : {}
  env_lambda_post_sleep_ms = trimspace(var.lambda_post_sleep_reserve_ms) != "" ? { LAMBDA_POST_SLEEP_RESERVE_MS = var.lambda_post_sleep_reserve_ms } : {}
  environment_variables = merge(
    {
      GEMINI_API_KEY       = var.gemini_api_key
      TELEGRAM_BOT_TOKEN   = var.telegram_bot_token
      TELEGRAM_CHAT_ID     = var.telegram_chat_id
    },
    local.env_gemini_model,
    local.env_idempotency_window,
    local.env_request_timeout,
    local.env_lambda_max_attempts,
    local.env_lambda_retry_m1,
    local.env_lambda_post_sleep_ms,
  )
}

resource "aws_lambda_function" "newsletter" {
  function_name    = var.function_name
  role             = aws_iam_role.lambda.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.12"
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  environment {
    variables = local.environment_variables
  }

  depends_on = [aws_iam_role_policy_attachment.lambda_basic]
}

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "${var.function_name}-schedule"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "lambda"
  arn       = aws_lambda_function.newsletter.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.newsletter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
