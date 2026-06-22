# Generated manually for deploy jobs

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0015_sync_dockercontainer_image_name_state"),
        ("dbmgr", "0004_databaseinstancehost_probe_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="DbDeployJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "job_type",
                    models.CharField(
                        choices=[("mysql_standalone", "MySQL 单实例"), ("oracle_standalone", "Oracle 单实例")],
                        max_length=32,
                        verbose_name="部署类型",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "待执行"),
                            ("prechecking", "预检查中"),
                            ("running", "执行中"),
                            ("verifying", "验证中"),
                            ("succeeded", "成功"),
                            ("failed", "失败"),
                            ("cancelled", "已取消"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="任务状态",
                    ),
                ),
                ("params", models.JSONField(default=dict, verbose_name="用户参数")),
                ("resolved_params", models.JSONField(blank=True, default=dict, verbose_name="合并参数快照")),
                ("result", models.JSONField(blank=True, default=dict, verbose_name="执行结果")),
                ("creator", models.CharField(blank=True, default="", max_length=50, verbose_name="创建人")),
                ("remark", models.TextField(blank=True, default="", verbose_name="备注")),
                ("error_message", models.CharField(blank=True, default="", max_length=512, verbose_name="错误信息")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "business",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="common.business",
                        verbose_name="所属业务",
                    ),
                ),
                (
                    "environment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="common.environment",
                        verbose_name="所属环境",
                    ),
                ),
                (
                    "instance",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="deploy_jobs",
                        to="dbmgr.databaseinstance",
                        verbose_name="注册实例",
                    ),
                ),
                (
                    "target_host",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="db_deploy_jobs",
                        to="common.host",
                        verbose_name="目标主机",
                    ),
                ),
            ],
            options={
                "verbose_name": "数据库部署任务",
                "verbose_name_plural": "数据库部署任务",
                "db_table": "dbmgr_deploy_job",
                "ordering": ["-id"],
            },
        ),
        migrations.CreateModel(
            name="DbDeployJobStep",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("step_code", models.CharField(max_length=32, verbose_name="步骤编码")),
                ("step_name", models.CharField(max_length=64, verbose_name="步骤名称")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "待执行"),
                            ("running", "执行中"),
                            ("succeeded", "成功"),
                            ("failed", "失败"),
                            ("skipped", "已跳过"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="步骤状态",
                    ),
                ),
                ("output", models.TextField(blank=True, default="", verbose_name="步骤输出")),
                ("sort_order", models.PositiveSmallIntegerField(default=0, verbose_name="排序")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="开始时间")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="结束时间")),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="steps",
                        to="dbmgr.dbdeployjob",
                        verbose_name="部署任务",
                    ),
                ),
            ],
            options={
                "verbose_name": "数据库部署步骤",
                "verbose_name_plural": "数据库部署步骤",
                "db_table": "dbmgr_deploy_job_step",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="dbdeployjobstep",
            constraint=models.UniqueConstraint(fields=("job", "step_code"), name="uniq_dbmgr_deploy_job_step"),
        ),
    ]
