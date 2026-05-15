output "lambda_function_name" {
  value = aws_lambda_function.newsletter.function_name
}

output "lambda_function_arn" {
  value = aws_lambda_function.newsletter.arn
}

output "event_rule_name" {
  value = aws_cloudwatch_event_rule.schedule.name
}
