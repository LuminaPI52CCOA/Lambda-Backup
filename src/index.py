import os
import gzip
import shutil
import logging
import subprocess
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo
import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")


def get_current_datetime() -> datetime:
    """Retorna a data e hora atual no fuso horário America/Sao_Paulo (UTC-3)."""
    return datetime.now(SAO_PAULO_TZ)


def resolve_mysqldump_binary() -> str:
    """
    Localiza o binário do mysqldump priorizando a Lambda Layer (/opt/bin/mysqldump)
    ou o PATH padrão do sistema operacional.
    """
    layer_path = os.environ.get("MYSQLDUMP_PATH", "/opt/bin/mysqldump")
    if os.path.isfile(layer_path) and os.access(layer_path, os.X_OK):
        return layer_path

    system_path = shutil.which("mysqldump")
    if system_path:
        return system_path

    return layer_path


def run_database_dump(
    db_host: str,
    db_port: str,
    db_user: str,
    db_password: str,
    db_name: str,
    output_gz_path: str,
) -> int:
    """
    Executa o mysqldump e comprime a saída em formato .sql.gz em streaming.
    Retorna o tamanho do arquivo gerado em bytes.
    """
    mysqldump_bin = resolve_mysqldump_binary()

    cmd = [
        mysqldump_bin,
        f"--host={db_host}",
        f"--port={db_port}",
        f"--user={db_user}",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--events",
        "--set-gtid-purged=OFF",
        db_name,
    ]

    env = os.environ.copy()
    env["MYSQL_PWD"] = db_password

    logger.info("Iniciando dump do schema '%s' no host '%s:%s'...", db_name, db_host, db_port)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    with gzip.open(output_gz_path, "wb") as gz_file:
        if process.stdout:
            shutil.copyfileobj(process.stdout, gz_file)

    _, stderr_bytes = process.communicate()
    stderr_output = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""

    if process.returncode != 0:
        raise RuntimeError(
            f"Falha na execução do mysqldump (exit code {process.returncode}): {stderr_output}"
        )

    if not os.path.exists(output_gz_path):
        raise RuntimeError("Arquivo de backup não foi gerado em /tmp.")

    file_size = os.path.getsize(output_gz_path)
    if file_size == 0:
        raise RuntimeError("O arquivo de backup gerado está vazio (0 bytes).")

    logger.info("Dump concluído com sucesso: %s (%d bytes)", output_gz_path, file_size)
    return file_size


def upload_to_s3(local_path: str, bucket_name: str, s3_key: str, s3_client=None) -> str:
    """Faz o upload do arquivo de backup para o bucket S3 especificado."""
    client = s3_client or boto3.client("s3")
    logger.info("Iniciando upload para s3://%s/%s...", bucket_name, s3_key)

    client.upload_file(
        Filename=local_path,
        Bucket=bucket_name,
        Key=s3_key,
        ExtraArgs={
            "ContentType": "application/gzip",
            "ServerSideEncryption": "AES256",
        },
    )

    s3_uri = f"s3://{bucket_name}/{s3_key}"
    logger.info("Upload finalizado com sucesso: %s", s3_uri)
    return s3_uri


def publish_notification(
    sns_topic_arn: str,
    subject: str,
    message: str,
    sns_client=None,
) -> None:
    """Publica notificação de status (sucesso ou falha) no tópico Amazon SNS."""
    if not sns_topic_arn:
        logger.warning("SNS_TOPIC_ARN não configurado. Notificação omitida.")
        return

    client = sns_client or boto3.client("sns")
    # O limite de Subject no SNS é de 100 caracteres ASCII
    safe_subject = subject[:100]
    client.publish(
        TopicArn=sns_topic_arn,
        Subject=safe_subject,
        Message=message,
    )
    logger.info("Notificação SNS enviada com sucesso para %s", sns_topic_arn)


def handler(event, context):
    """
    AWS Lambda Handler principal para a rotina automatizada de backup do banco Lumina.
    """
    now = get_current_datetime()
    date_iso = now.strftime("%Y-%m-%d")
    timestamp_formatted = now.strftime("%Y-%m-%d %H:%M:%S %Z")

    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "3306")
    db_name = os.environ.get("DB_NAME", "Lumina")
    db_user = os.environ.get("DB_USER", "lumina_user")
    db_password = os.environ.get("DB_PASSWORD", "")
    s3_bucket = os.environ.get("S3_BUCKET_NAME", "lumina-sptech-backup-prod")
    s3_prefix = os.environ.get("S3_PREFIX", "backups").strip("/")
    sns_topic_arn = os.environ.get("SNS_TOPIC_ARN", "")

    filename = f"lumina-backup-{date_iso}.sql.gz"
    local_tmp_path = f"/tmp/{filename}"
    s3_key = f"{s3_prefix}/{filename}" if s3_prefix else filename

    try:
        if not db_password:
            raise ValueError("A variável de ambiente obrigatória DB_PASSWORD não foi informada.")

        file_size_bytes = run_database_dump(
            db_host=db_host,
            db_port=db_port,
            db_user=db_user,
            db_password=db_password,
            db_name=db_name,
            output_gz_path=local_tmp_path,
        )

        file_size_mb = round(file_size_bytes / (1024 * 1024), 4)

        s3_uri = upload_to_s3(
            local_path=local_tmp_path,
            bucket_name=s3_bucket,
            s3_key=s3_key,
        )

        subject = f"[Lumina Backup] SUCESSO - {date_iso}"
        message = (
            "Relatório de Backup Automatizado - Sistema Lumina Odontológica\n"
            "==============================================================\n"
            "Status: SUCESSO\n"
            f"Data/Hora (BRT): {timestamp_formatted}\n"
            f"Banco de Dados (Schema): {db_name}\n"
            f"Host de Origem: {db_host}:{db_port}\n"
            f"Arquivo Gerado: {filename}\n"
            f"Tamanho Compactado: {file_size_bytes} bytes ({file_size_mb} MB)\n"
            f"Destino S3: {s3_uri}\n"
            "Política de Retenção: 30 dias (S3 Lifecycle)\n"
        )

        publish_notification(
            sns_topic_arn=sns_topic_arn,
            subject=subject,
            message=message,
        )

        return {
            "statusCode": 200,
            "status": "SUCCESS",
            "backupFile": filename,
            "s3Uri": s3_uri,
            "sizeBytes": file_size_bytes,
            "timestamp": timestamp_formatted,
        }

    except ( Exception, BotoCoreError, ClientError ) as exc:
        error_trace = traceback.format_exc()
        logger.error("Erro durante a execução do backup: %s\n%s", str(exc), error_trace)

        subject = f"[Lumina Backup] FALHA - {date_iso}"
        message = (
            "Alerta de Falha no Backup Automatizado - Sistema Lumina Odontológica\n"
            "====================================================================\n"
            "Status: FALHA\n"
            f"Data/Hora (BRT): {timestamp_formatted}\n"
            f"Banco de Dados (Schema): {db_name}\n"
            f"Host de Origem: {db_host}:{db_port}\n"
            f"Arquivo Alvo: {filename}\n"
            f"Motivo do Erro: {str(exc)}\n\n"
            "Detalhes Técnicos (Traceback):\n"
            f"{error_trace}\n"
        )

        try:
            publish_notification(
                sns_topic_arn=sns_topic_arn,
                subject=subject,
                message=message,
            )
        except Exception as sns_exc:
            logger.error("Falha crítica ao tentar enviar notificação SNS de erro: %s", str(sns_exc))

        raise

    finally:
        if os.path.exists(local_tmp_path):
            try:
                os.remove(local_tmp_path)
                logger.info("Arquivo temporário removido de /tmp: %s", local_tmp_path)
            except OSError as cleanup_err:
                logger.warning("Não foi possível remover %s: %s", local_tmp_path, str(cleanup_err))
