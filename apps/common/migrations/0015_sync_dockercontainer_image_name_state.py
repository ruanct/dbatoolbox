from django.db import migrations


class Migration(migrations.Migration):
    """同步 docker_image → image_name 的迁移状态。

    列名已在 0009 通过 RunSQL 修改，此处仅更新 Django state，不操作数据库。
    """

    dependencies = [
        ("common", "0014_add_batch_forks_duration"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameField(
                    model_name="dockercontainer",
                    old_name="docker_image",
                    new_name="image_name",
                ),
            ],
        ),
    ]
