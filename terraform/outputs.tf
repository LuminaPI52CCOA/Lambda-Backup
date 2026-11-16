output "lambda_backup_arn" {
  value       = aws_lambda_function.database_backup.arn
  description = "ARN da funcao AWS Lambda de backup do banco de dados"
}

output "lambda_backup_function_name" {
  value       = aws_lambda_function.database_backup.function_name
  description = "Nome da funcao AWS Lambda de backup do banco de dados"
}

output "backup_bucket_name" {
  value       = aws_cloudformation_stack.backup_bucket.outputs["BucketName"]
  description = "Nome do bucket S3 dedicado aos backups"
}

output "backup_bucket_arn" {
  value       = aws_cloudformation_stack.backup_bucket.outputs["BucketArn"]
  description = "ARN do bucket S3 dedicado aos backups"
}

output "sns_topic_arn" {
  value       = aws_sns_topic.backup_notifications.arn
  description = "ARN do topico Amazon SNS de notificacoes de backup"
}

output "lambda_security_group_id" {
  value       = aws_security_group.lambda_backup.id
  description = "ID do Security Group exclusivo da Lambda de backup"
}
