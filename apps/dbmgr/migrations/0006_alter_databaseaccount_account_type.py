from django.db import migrations, models

LEGACY_ACCOUNT_TYPE_MAP = {
    "admin": "user_dba",
    "readonly": "user_read",
    "app": "user_app",
    "backup": "user_bak",
}


def migrate_account_types_forward(apps, schema_editor):
    DatabaseAccount = apps.get_model("dbmgr", "DatabaseAccount")
    for account in DatabaseAccount.objects.all().only("id", "account_type"):
        new_type = LEGACY_ACCOUNT_TYPE_MAP.get(account.account_type)
        if new_type:
            account.account_type = new_type
            account.save(update_fields=["account_type"])


def migrate_account_types_backward(apps, schema_editor):
    reverse_map = {value: key for key, value in LEGACY_ACCOUNT_TYPE_MAP.items()}
    DatabaseAccount = apps.get_model("dbmgr", "DatabaseAccount")
    for account in DatabaseAccount.objects.all().only("id", "account_type"):
        old_type = reverse_map.get(account.account_type)
        if old_type:
            account.account_type = old_type
            account.save(update_fields=["account_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("dbmgr", "0005_dbdeployjob_dbdeployjobstep"),
    ]

    operations = [
        migrations.RunPython(migrate_account_types_forward, migrate_account_types_backward),
        migrations.AlterField(
            model_name="databaseaccount",
            name="account_type",
            field=models.CharField(
                choices=[
                    ("user_adm", "超级管理员"),
                    ("user_dba", "高级DBA用户"),
                    ("user_ops", "日常运维用户"),
                    ("user_app", "业务应用账号"),
                    ("user_read", "只读分析账号"),
                    ("user_repl", "数据复制账号"),
                    ("user_exp", "数据导出账号"),
                    ("user_mon", "系统监控账号"),
                    ("user_bak", "系统备份账号"),
                ],
                default="user_dba",
                max_length=16,
                verbose_name="账号类型",
            ),
        ),
    ]
