
启动方式

# 终端1：启动 Django
.venv/bin/python manage.py runserver

# 终端2：启动 Celery Worker
.venv/bin/celery -A dbatoolbox worker -l info

# 终端3：启动 Celery Beat（定时探测，可选；页面也会每 60 秒自动探测）
.venv/bin/celery -A dbatoolbox beat -l info

