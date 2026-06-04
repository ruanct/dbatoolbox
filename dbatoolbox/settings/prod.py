# 导入所有公共配置（继承base）
from .base import *

# 生产环境强制关闭调试（安全生产红线）
DEBUG = False

# 生产环境按需填写真实域名/IP，禁止使用 *
ALLOWED_HOSTS = []


# 生产环境静态归集目录
STATIC_ROOT = BASE_DIR / "static_collect"

# 生产开启哈希缓存，解决浏览器缓存问题
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'

