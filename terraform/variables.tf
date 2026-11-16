variable "project_name" {
  type        = string
  description = "Nome base do projeto"
  default     = "lumina"
}

variable "environment" {
  type        = string
  description = "Ambiente de implantacao (exclusivo para prod)"
  default     = "prod"
}

variable "vpc_id" {
  type        = string
  description = "ID da VPC onde a Lambda e o Security Group serao alocados"
}

variable "subnet_ids" {
  type        = list(string)
  description = "Lista de IDs das sub-redes privadas para execucao da Lambda"
}

variable "db_sg_id" {
  type        = string
  description = "ID do Security Group do banco de dados MySQL para liberacao da porta 3306"
}

variable "db_host" {
  type        = string
  description = "IP privado ou hostname da instancia primaria do MySQL"
}

variable "db_port" {
  type        = string
  description = "Porta de conexao do MySQL"
  default     = "3306"
}

variable "db_name" {
  type        = string
  description = "Nome do schema do banco de dados (case-sensitive: Lumina)"
  default     = "Lumina"
}

variable "db_user" {
  type        = string
  description = "Usuario de autenticacao no MySQL"
  default     = "lumina_user"
}

variable "db_password" {
  type        = string
  description = "Senha de autenticacao no MySQL"
  sensitive   = true
}

variable "backup_bucket_name" {
  type        = string
  description = "Nome base do bucket S3 dedicado aos backups"
  default     = "lumina-sptech-backup-prod"
}

variable "append_account_id" {
  type        = bool
  description = "Anexa o Account ID da AWS ao nome do bucket para garantir unicidade global"
  default     = true
}

variable "backup_retention_days" {
  type        = number
  description = "Quantidade de dias para retencao dos arquivos de backup no S3 (Estrategia A)"
  default     = 30
}

variable "admin_email" {
  type        = string
  description = "E-mail do administrador para receber notificacoes via Amazon SNS"
  default     = "grupo5pi-2ccoa@outlook.com"
}

variable "schedule_expression" {
  type        = string
  description = "Expressao cron no EventBridge (06:00 UTC = 03:00 America/Sao_Paulo)"
  default     = "cron(0 6 * * ? *)"
}

variable "layer_s3_bucket" {
  type        = string
  description = "Bucket S3 opcional onde o pacote .zip da Lambda Layer (mysqldump) esta hospedado"
  default     = ""
}

variable "layer_s3_key" {
  type        = string
  description = "Chave S3 opcional do arquivo .zip da Lambda Layer (mysqldump)"
  default     = ""
}
