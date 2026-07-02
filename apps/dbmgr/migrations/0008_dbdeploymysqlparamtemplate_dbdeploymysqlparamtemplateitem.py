import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dbmgr", "0007_databaseinstance_server_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="DbDeployMysqlParamTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("template_code", models.CharField(max_length=64, unique=True, verbose_name="模板编码")),
                ("title", models.CharField(max_length=128, verbose_name="模板标题")),
                (
                    "major_version",
                    models.CharField(
                        choices=[("5.7", "MySQL 5.7"), ("8.0", "MySQL 8.0")],
                        max_length=16,
                        verbose_name="MySQL major 版本",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("enabled", "启用"), ("disabled", "禁用")],
                        default="enabled",
                        max_length=16,
                        verbose_name="状态",
                    ),
                ),
                ("is_default", models.BooleanField(default=False, verbose_name="同 major 默认模板")),
                ("remark", models.TextField(blank=True, default="", verbose_name="备注")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "MySQL 部署参数模板",
                "verbose_name_plural": "MySQL 部署参数模板",
                "db_table": "dbmgr_deploy_mysql_param_template",
                "ordering": ["major_version", "title", "id"],
            },
        ),
        migrations.CreateModel(
            name="DbDeployMysqlParamTemplateItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sort_order", models.PositiveSmallIntegerField(default=0, verbose_name="排序")),
                (
                    "section",
                    models.CharField(
                        choices=[("mysqld", "[mysqld]"), ("client", "[client]")],
                        default="mysqld",
                        max_length=32,
                        verbose_name="配置段",
                    ),
                ),
                ("param_name", models.CharField(max_length=128, verbose_name="参数名")),
                ("param_value", models.CharField(max_length=512, verbose_name="参数值")),
                ("default_value", models.CharField(blank=True, default="", max_length=512, verbose_name="参考默认值")),
                ("remark", models.CharField(blank=True, default="", max_length=256, verbose_name="备注")),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="dbmgr.dbdeploymysqlparamtemplate",
                        verbose_name="所属模板",
                    ),
                ),
            ],
            options={
                "verbose_name": "MySQL 部署参数模板明细",
                "verbose_name_plural": "MySQL 部署参数模板明细",
                "db_table": "dbmgr_deploy_mysql_param_template_item",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="dbdeploymysqlparamtemplateitem",
            constraint=models.UniqueConstraint(
                fields=("template", "section", "param_name"),
                name="uniq_dbmgr_mysql_param_tpl_item",
            ),
        ),
    ]
