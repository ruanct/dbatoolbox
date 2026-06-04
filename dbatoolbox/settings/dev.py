# 导入所有公共配置（继承base）
from .base import *

# 开发环境专属覆盖
DEBUG = True

# 开发环境允许所有IP访问
ALLOWED_HOSTS = ["*"]


# 开发环境开启公共静态目录
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# 开发不启用静态归集、不配置STATIC_ROOT

