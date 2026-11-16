import io
import os
import sys
import gzip
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import index as backup_module


class TestLambdaBackup(unittest.TestCase):
    def test_get_current_datetime_uses_sao_paulo_timezone(self):
        now = backup_module.get_current_datetime()
        self.assertEqual(now.tzinfo, ZoneInfo("America/Sao_Paulo"))

    def test_resolve_mysqldump_binary_prefers_layer(self):
        with patch.dict(os.environ, {"MYSQLDUMP_PATH": "/opt/bin/mysqldump"}), patch(
            "os.path.isfile", return_value=True
        ), patch("os.access", return_value=True):
            self.assertEqual(backup_module.resolve_mysqldump_binary(), "/opt/bin/mysqldump")

    def test_resolve_mysqldump_binary_fallback_to_system_path(self):
        with patch.dict(os.environ, {"MYSQLDUMP_PATH": "/opt/bin/nonexistent"}), patch(
            "os.path.isfile", return_value=False
        ), patch("shutil.which", return_value="/usr/bin/mysqldump"):
            self.assertEqual(backup_module.resolve_mysqldump_binary(), "/usr/bin/mysqldump")

    def test_run_database_dump_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_gz = os.path.join(tmp_dir, "lumina-backup-2026-11-12.sql.gz")
            sql_payload = b"-- MySQL dump\nCREATE TABLE paciente (id INT PRIMARY KEY);\n"

            mock_proc = MagicMock()
            mock_proc.stdout = io.BytesIO(sql_payload)
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0

            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                size = backup_module.run_database_dump(
                    db_host="10.0.20.10",
                    db_port="3306",
                    db_user="lumina_user",
                    db_password="secret_password",
                    db_name="Lumina",
                    output_gz_path=output_gz,
                )

            self.assertGreater(size, 0)
            self.assertTrue(os.path.exists(output_gz))
            with gzip.open(output_gz, "rb") as f:
                decompressed = f.read()
            self.assertEqual(decompressed, sql_payload)

            called_args, called_kwargs = mock_popen.call_args
            self.assertIn("Lumina", called_args[0])
            self.assertIn("--single-transaction", called_args[0])
            self.assertEqual(called_kwargs["env"]["MYSQL_PWD"], "secret_password")

    def test_run_database_dump_raises_on_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_gz = os.path.join(tmp_dir, "failed-backup.sql.gz")

            mock_proc = MagicMock()
            mock_proc.stdout = io.BytesIO(b"")
            mock_proc.communicate.return_value = (
                b"",
                b"mysqldump: Got error: 1045: Access denied",
            )
            mock_proc.returncode = 2

            with patch("subprocess.Popen", return_value=mock_proc):
                with self.assertRaises(RuntimeError) as ctx:
                    backup_module.run_database_dump(
                        db_host="10.0.20.10",
                        db_port="3306",
                        db_user="lumina_user",
                        db_password="wrong_password",
                        db_name="Lumina",
                        output_gz_path=output_gz,
                    )
                self.assertIn("Access denied", str(ctx.exception))

    def test_upload_to_s3_calls_boto3_correctly(self):
        mock_s3 = MagicMock()
        uri = backup_module.upload_to_s3(
            local_path="/tmp/lumina-backup-2026-11-12.sql.gz",
            bucket_name="lumina-sptech-backup-prod",
            s3_key="backups/lumina-backup-2026-11-12.sql.gz",
            s3_client=mock_s3,
        )

        self.assertEqual(
            uri, "s3://lumina-sptech-backup-prod/backups/lumina-backup-2026-11-12.sql.gz"
        )
        mock_s3.upload_file.assert_called_once_with(
            Filename="/tmp/lumina-backup-2026-11-12.sql.gz",
            Bucket="lumina-sptech-backup-prod",
            Key="backups/lumina-backup-2026-11-12.sql.gz",
            ExtraArgs={
                "ContentType": "application/gzip",
                "ServerSideEncryption": "AES256",
            },
        )

    def test_publish_notification_sends_sns_message(self):
        mock_sns = MagicMock()
        backup_module.publish_notification(
            sns_topic_arn="arn:aws:sns:us-east-1:123456789012:lumina-backup-notifications-prod",
            subject="[Lumina Backup] SUCESSO - 2026-11-12",
            message="Backup concluído",
            sns_client=mock_sns,
        )

        mock_sns.publish.assert_called_once_with(
            TopicArn="arn:aws:sns:us-east-1:123456789012:lumina-backup-notifications-prod",
            Subject="[Lumina Backup] SUCESSO - 2026-11-12",
            Message="Backup concluído",
        )

    def test_handler_end_to_end_success(self):
        fixed_dt = datetime(2026, 11, 12, 3, 0, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
        env_vars = {
            "DB_HOST": "10.0.20.15",
            "DB_PORT": "3306",
            "DB_NAME": "Lumina",
            "DB_USER": "lumina_user",
            "DB_PASSWORD": "prod_pass_123",
            "S3_BUCKET_NAME": "lumina-sptech-backup-prod",
            "S3_PREFIX": "backups",
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:lumina-backup-notifications-prod",
        }

        sql_data = b"CREATE DATABASE IF NOT EXISTS Lumina;\nUSE Lumina;\n"
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(sql_data)
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        mock_s3_client = MagicMock()
        mock_sns_client = MagicMock()

        def fake_boto3_client(service_name, *args, **kwargs):
            if service_name == "s3":
                return mock_s3_client
            if service_name == "sns":
                return mock_sns_client
            raise ValueError(f"Unexpected service: {service_name}")

        with patch.dict(os.environ, env_vars, clear=False), patch.object(
            backup_module, "get_current_datetime", return_value=fixed_dt
        ), patch("subprocess.Popen", return_value=mock_proc), patch(
            "boto3.client", side_effect=fake_boto3_client
        ):
            response = backup_module.handler({}, {})

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["status"], "SUCCESS")
        self.assertEqual(response["backupFile"], "lumina-backup-2026-11-12.sql.gz")
        self.assertEqual(
            response["s3Uri"],
            "s3://lumina-sptech-backup-prod/backups/lumina-backup-2026-11-12.sql.gz",
        )
        self.assertGreater(response["sizeBytes"], 0)

        mock_s3_client.upload_file.assert_called_once()
        mock_sns_client.publish.assert_called_once()
        sns_call_kwargs = mock_sns_client.publish.call_args.kwargs
        self.assertIn("SUCESSO - 2026-11-12", sns_call_kwargs["Subject"])
        self.assertIn("lumina-backup-2026-11-12.sql.gz", sns_call_kwargs["Message"])
        self.assertFalse(os.path.exists("/tmp/lumina-backup-2026-11-12.sql.gz"))

    def test_handler_notifies_sns_and_reraises_on_failure(self):
        fixed_dt = datetime(2026, 11, 12, 3, 0, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
        env_vars = {
            "DB_HOST": "10.0.20.15",
            "DB_NAME": "Lumina",
            "DB_USER": "lumina_user",
            "DB_PASSWORD": "prod_pass_123",
            "S3_BUCKET_NAME": "lumina-sptech-backup-prod",
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:lumina-backup-notifications-prod",
        }

        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"")
        mock_proc.communicate.return_value = (
            b"",
            b"Can't connect to MySQL server on '10.0.20.15'",
        )
        mock_proc.returncode = 2

        mock_sns_client = MagicMock()

        with patch.dict(os.environ, env_vars, clear=False), patch.object(
            backup_module, "get_current_datetime", return_value=fixed_dt
        ), patch("subprocess.Popen", return_value=mock_proc), patch(
            "boto3.client", return_value=mock_sns_client
        ):
            with self.assertRaises(RuntimeError) as ctx:
                backup_module.handler({}, {})
            self.assertIn("Can't connect to MySQL server", str(ctx.exception))

        mock_sns_client.publish.assert_called_once()
        sns_call_kwargs = mock_sns_client.publish.call_args.kwargs
        self.assertIn("FALHA - 2026-11-12", sns_call_kwargs["Subject"])
        self.assertIn("Can't connect to MySQL server", sns_call_kwargs["Message"])
        self.assertFalse(os.path.exists("/tmp/lumina-backup-2026-11-12.sql.gz"))


if __name__ == "__main__":
    unittest.main()
