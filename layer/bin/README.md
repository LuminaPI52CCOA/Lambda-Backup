# Lambda Layer - Utilitário mysqldump

Coloque aqui o binário compilado `mysqldump` (compatível com Amazon Linux 2023 x86_64) ou configure as variáveis `layer_s3_bucket` e `layer_s3_key` no Terraform para provisionar a Layer diretamente a partir de um bucket S3.

Quando montada na AWS Lambda, esta Layer disponibiliza seus binários em `/opt/bin/mysqldump`.
