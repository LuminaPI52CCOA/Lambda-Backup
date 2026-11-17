# Lambda-Backup — Rotina Automatizada de Backup do Banco de Dados Lumina

Repositório oficial do componente serverless responsável pelo backup diário automatizado do banco de dados **MySQL 8.0 (`Lumina`)**, armazenamento seguro no **Amazon S3** com política de retenção de 30 dias e notificações de auditoria via **Amazon SNS**.

---

## 1. Arquitetura e Fluxo de Execução

1. **Gatilho (Amazon EventBridge):** Dispara diariamente às **03:00 BRT** (`cron(0 6 * * ? *)` UTC).
2. **Execução (AWS Lambda Python 3.11):**
   - Alocada nas sub-redes privadas de aplicação (`private_app_subnet_ids`) da VPC de produção.
   - Utiliza Security Group dedicado (`lumina-sg-lambda-backup-prod`) com permissão restrita à porta `3306` do MySQL.
   - Invoca o binário `mysqldump` disponibilizado via **AWS Lambda Layer** (`/opt/bin/mysqldump`) e comprime o dump em streaming para `/tmp/lumina-backup-YYYY-MM-DD.sql.gz`.
3. **Armazenamento (Amazon S3):**
   - Faz o upload com criptografia `AES256` para o bucket exclusivo `lumina-sptech-backup-prod-<account_id>` através do **VPC Endpoint Gateway S3** (sem custo de tráfego NAT).
   - Regra de Ciclo de Vida (**Estratégia A**): Expiração automática após **30 dias**.
4. **Notificação (Amazon SNS):**
   - Publica relatório de **SUCESSO** (com nome do arquivo, tamanho em MB, URI S3 e horário BRT) ou alerta de **FALHA** (com motivo e traceback) no tópico `lumina-backup-notifications-prod`, encaminhado para `grupo5pi-2ccoa@sptech.school`.

> **Nota de Governança:** Em conformidade com as restrições arquiteturais do projeto Lumina, este componente é provisionado **exclusivamente no ambiente `prod`**, sendo omitido em `dev`.

---

## 2. Estrutura do Repositório

```text
Lambda-Backup/
├── .github/
│   └── workflows/
│       ├── backup-ci.yml          # CI: Flake8 + Testes Unitários Isolados
│       └── backup-cd.yml          # CD: Atualização rápida de artefato na AWS Lambda
├── layer/
│   └── bin/                       # Diretório base para o binário mysqldump da Layer
├── src/
│   ├── index.py                   # Handler principal da Lambda (Python 3.11)
│   └── requirements.txt           # Dependências do runtime
├── test/
│   └── test_backup.py             # Suíte de testes unitários com mocks
├── terraform/
│   ├── main.tf                    # Recursos AWS (Lambda, Layer, SG, S3, SNS, EventBridge)
│   ├── variables.tf               # Variáveis de entrada do módulo
│   ├── outputs.tf                 # ARNs e nomes exportados para a Infraestrutura
│   └── terraform.tfvars.example   # Exemplo de configuração
└── README.md
```

---

## 3. Variáveis de Ambiente da Lambda

| Variável | Descrição | Valor Padrão |
| :--- | :--- | :--- |
| `DB_HOST` | IP privado ou hostname do servidor MySQL primário | Injetado via Terraform |
| `DB_PORT` | Porta TCP do MySQL | `3306` |
| `DB_NAME` | Nome do schema (case-sensitive) | `Lumina` |
| `DB_USER` | Usuário de conexão ao banco | `lumina_user` |
| `DB_PASSWORD` | Senha do banco de dados | Injetado via Terraform |
| `S3_BUCKET_NAME` | Nome do bucket S3 de destino dos backups | `lumina-sptech-backup-prod-<id>` |
| `S3_PREFIX` | Prefixo/pasta dentro do bucket | `backups` |
| `SNS_TOPIC_ARN` | ARN do tópico SNS para notificações de sucesso/erro | Injetado via Terraform |
| `MYSQLDUMP_PATH` | Caminho do binário na Lambda Layer | `/opt/bin/mysqldump` |
| `TZ` | Fuso horário oficial do sistema Lumina | `America/Sao_Paulo` |

---

## 4. Como Executar os Testes Localmente

A suíte de testes utiliza `unittest.mock` para simular o processo `mysqldump`, o cliente S3 e o cliente SNS, não exigindo conexão real com a AWS ou MySQL:

```bash
python3 -m unittest discover -s test -v
```
