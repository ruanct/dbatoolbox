from django.db import migrations, models


def set_mysql_grant_host_default(apps, schema_editor):
    DatabaseAccount = apps.get_model("dbmgr", "DatabaseAccount")
    DatabaseInstance = apps.get_model("dbmgr", "DatabaseInstance")
    mysql_instance_ids = DatabaseInstance.objects.filter(engine="mysql").values_list("id", flat=True)
    DatabaseAccount.objects.filter(
        instance_id__in=mysql_instance_ids,
        grant_host="",
    ).update(grant_host="%")


class Migration(migrations.Migration):

    dependencies = [
        ("dbmgr", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="databaseaccount",
            name="grant_host",
            field=models.CharField(
                blank=True,
                default="",
                help_text="MySQL 专用，对应 mysql.user.Host，如 %、localhost、10.1.%",
                max_length=255,
                verbose_name="授权主机",
            ),
        ),
        migrations.RunPython(set_mysql_grant_host_default, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="databaseaccount",
            name="uniq_dbmgr_instance_account_name",
        ),
        migrations.AddConstraint(
            model_name="databaseaccount",
            constraint=models.UniqueConstraint(
                fields=("instance", "account_name", "grant_host"),
                name="uniq_dbmgr_instance_account_identity",
            ),
        ),
    ]
