from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dbmgr", "0003_databaseinstance_probe_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="databaseinstancehost",
            name="last_probed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="最后探测时间"),
        ),
        migrations.AddField(
            model_name="databaseinstancehost",
            name="latency_ms",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="探测延迟(ms)"),
        ),
        migrations.AddField(
            model_name="databaseinstancehost",
            name="probe_message",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="探测信息"),
        ),
        migrations.AddField(
            model_name="databaseinstancehost",
            name="probe_status",
            field=models.CharField(
                choices=[
                    ("alive", "正常"),
                    ("dead", "异常"),
                    ("unknown", "未探测"),
                    ("maintenance", "维护中"),
                ],
                default="unknown",
                max_length=16,
                verbose_name="探测状态",
            ),
        ),
    ]
