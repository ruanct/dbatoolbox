from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dbmgr", "0008_dbdeploymysqlparamtemplate_dbdeploymysqlparamtemplateitem"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dbdeployjob",
            name="job_type",
            field=models.CharField(
                choices=[
                    ("mysql_standalone", "MySQL 单实例"),
                    ("mysql_replica", "MySQL 从库"),
                    ("oracle_standalone", "Oracle 单实例"),
                ],
                max_length=32,
                verbose_name="部署类型",
            ),
        ),
    ]
