
启动方式

# 终端1：启动 Django
.venv/bin/python manage.py runserver

# 终端2：启动 Celery Worker
.venv/bin/celery -A dbatoolbox worker -l info


