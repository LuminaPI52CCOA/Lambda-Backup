terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

data "aws_caller_identity" "current" {}

# Regra 1 de Ouro: Reutilizacao estrita da LabRole da AWS Academy Learner Lab
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

locals {
  resolved_bucket_name = var.append_account_id ? "${var.backup_bucket_name}-${data.aws_caller_identity.current.account_id}" : var.backup_bucket_name
  use_s3_layer         = var.layer_s3_bucket != "" && var.layer_s3_key != ""
}

# ==============================================================================
# 1. Bucket S3 Dedicado para Backups com Lifecycle de 30 Dias (Estrategia A)
# Provisionado via CloudFormation para contornar bloqueio SCP GetObjectLock no Vocareum
# ==============================================================================

resource "aws_cloudformation_stack" "backup_bucket" {
  name = "cf-s3-${local.resolved_bucket_name}"

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Description              = "S3 Bucket dedicado para backups do MySQL Lumina com Lifecycle de ${var.backup_retention_days} dias"
    Resources = {
      BackupBucket = {
        Type = "AWS::S3::Bucket"
        Properties = {
          BucketName = local.resolved_bucket_name
          PublicAccessBlockConfiguration = {
            BlockPublicAcls       = true
            BlockPublicPolicy     = true
            IgnorePublicAcls      = true
            RestrictPublicBuckets = true
          }
          LifecycleConfiguration = {
            Rules = [
              {
                Id               = "ExpireDatabaseBackupsAfter${var.backup_retention_days}Days"
                Status           = "Enabled"
                ExpirationInDays = var.backup_retention_days
              }
            ]
          }
          Tags = [
            {
              Key   = "Name"
              Value = local.resolved_bucket_name
            },
            {
              Key   = "Environment"
              Value = var.environment
            },
            {
              Key   = "Component"
              Value = "Database-Backup"
            }
          ]
        }
      }
    }
    Outputs = {
      BucketName = {
        Value       = { "Ref" = "BackupBucket" }
        Description = "Nome do bucket S3 de backups"
      }
      BucketArn = {
        Value       = { "Fn::GetAtt" = ["BackupBucket", "Arn"] }
        Description = "ARN do bucket S3 de backups"
      }
    }
  })

  tags = {
    Name        = local.resolved_bucket_name
    Environment = var.environment
    Component   = "Database-Backup"
  }
}

# ==============================================================================
# 2. Rede e Seguranca (Security Group da Lambda e Regra de Acesso ao MySQL)
# ==============================================================================

resource "aws_security_group" "lambda_backup" {
  name        = "${var.project_name}-sg-lambda-backup-${var.environment}"
  description = "Security Group dedicado para a Lambda de Backup do Banco de Dados"
  vpc_id      = var.vpc_id

  egress {
    description = "Saida para MySQL (3306), VPC Endpoint S3 e Amazon SNS via HTTPS"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-sg-lambda-backup-${var.environment}"
    Environment = var.environment
  }
}

resource "aws_security_group_rule" "allow_lambda_backup_to_mysql" {
  type                     = "ingress"
  description              = "MySQL 3306 a partir da Lambda de Backup Automatizado"
  from_port                = tonumber(var.db_port)
  to_port                  = tonumber(var.db_port)
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.lambda_backup.id
  security_group_id        = var.db_sg_id
}

# ==============================================================================
# 3. Notificacao por E-mail via Amazon SNS
# ==============================================================================

resource "aws_sns_topic" "backup_notifications" {
  name = "${var.project_name}-backup-notifications-${var.environment}"

  tags = {
    Name        = "${var.project_name}-backup-notifications-${var.environment}"
    Environment = var.environment
    Component   = "Database-Backup"
  }
}

resource "aws_sns_topic_subscription" "admin_email" {
  topic_arn = aws_sns_topic.backup_notifications.arn
  protocol  = "email"
  endpoint  = var.admin_email
}

# ==============================================================================
# 4. Lambda Layer (Utilitario mysqldump - Abordagem 1)
# ==============================================================================

data "archive_file" "mysqldump_layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../layer"
  output_path = "${path.module}/dist/mysqldump_layer.zip"
}

resource "aws_lambda_layer_version" "mysqldump" {
  layer_name          = "${var.project_name}-mysqldump-layer-${var.environment}"
  description         = "Layer contendo utilitario mysqldump para backup do schema Lumina"
  compatible_runtimes = ["python3.11"]

  filename         = local.use_s3_layer ? null : data.archive_file.mysqldump_layer_zip.output_path
  source_code_hash = local.use_s3_layer ? null : data.archive_file.mysqldump_layer_zip.output_base64sha256
  s3_bucket        = local.use_s3_layer ? var.layer_s3_bucket : null
  s3_key           = local.use_s3_layer ? var.layer_s3_key : null
}

# ==============================================================================
# 5. Funcao AWS Lambda de Backup (Python 3.11)
# ==============================================================================

data "archive_file" "lambda_backup_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/dist/lambda_backup.zip"
  excludes    = ["__pycache__", "*.pyc", ".pytest_cache"]
}

resource "aws_lambda_function" "database_backup" {
  function_name    = "${var.project_name}-backup-${var.environment}"
  description      = "Rotina automatizada de backup diario (.sql.gz) do banco MySQL Lumina"
  role             = data.aws_iam_role.lab_role.arn
  handler          = "index.handler"
  runtime          = "python3.11"
  filename         = data.archive_file.lambda_backup_zip.output_path
  source_code_hash = data.archive_file.lambda_backup_zip.output_base64sha256
  timeout          = 300
  memory_size      = 512

  layers = [aws_lambda_layer_version.mysqldump.arn]

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [aws_security_group.lambda_backup.id]
  }

  environment {
    variables = {
      DB_HOST        = var.db_host
      DB_PORT        = var.db_port
      DB_NAME        = var.db_name
      DB_USER        = var.db_user
      DB_PASSWORD    = var.db_password
      S3_BUCKET_NAME = aws_cloudformation_stack.backup_bucket.outputs["BucketName"]
      S3_PREFIX      = "backups"
      SNS_TOPIC_ARN  = aws_sns_topic.backup_notifications.arn
      MYSQLDUMP_PATH = "/opt/bin/mysqldump"
      TZ             = "America/Sao_Paulo"
    }
  }

  tags = {
    Name        = "${var.project_name}-backup-${var.environment}"
    Projeto     = "Lumina Odontológica"
    Component   = "Lambda-Backup"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_log_group" "lambda_backup_logs" {
  name              = "/aws/lambda/${aws_lambda_function.database_backup.function_name}"
  retention_in_days = 14
}

# ==============================================================================
# 6. Agendamento Diario via Amazon EventBridge (03:00 BRT = 06:00 UTC)
# ==============================================================================

resource "aws_cloudwatch_event_rule" "daily_backup" {
  name                = "${var.project_name}-daily-db-backup-${var.environment}"
  description         = "Dispara o backup diario do banco Lumina as 03:00 BRT (06:00 UTC)"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "trigger_lambda_backup" {
  rule      = aws_cloudwatch_event_rule.daily_backup.name
  target_id = "TriggerLambdaDatabaseBackup"
  arn       = aws_lambda_function.database_backup.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridgeDailyBackup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.database_backup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_backup.arn
}
