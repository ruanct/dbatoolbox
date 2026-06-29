from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dbmgr", "0006_alter_databaseaccount_account_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="databaseinstance",
            name="server_id",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="MySQL 复制 server_id，仅 engine=mysql 且开启 binlog 时登记",
                null=True,
                verbose_name="MySQL server_id",
            ),
        ),
        migrations.AddIndex(
            model_name="databaseinstance",
            index=models.Index(fields=["engine", "server_id"], name="dbmgr_db_engine_server_idx"),
        ),
    ]
