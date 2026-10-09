import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
config_path = os.environ.get('LABWORK_CONFIG')
CONFIG = json.loads(Path(config_path).read_text(encoding='utf-8-sig')) if config_path else {}


def setting(name, default=''):
    return os.environ.get(name, CONFIG.get(name, default))


APP_ENV = setting('APP_ENV', 'dev')
if APP_ENV not in {'dev', 'test', 'prod'}:
    raise ValueError('APP_ENV 必须为 dev、test 或 prod')
RUNTIME_DIR = Path(setting('RUNTIME_DIR', str(BASE_DIR / 'runtime' / APP_ENV))).resolve()
WORKBOOK_ROOT = Path(setting('WORKBOOK_ROOT', str(RUNTIME_DIR / 'workbooks'))).resolve()
if APP_ENV != 'prod' and not WORKBOOK_ROOT.is_relative_to(RUNTIME_DIR):
    raise ValueError('开发和测试只能使用当前运行目录中的工作簿副本')
WORKBOOK_ROOT.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY = setting('SECRET_KEY', 'development-only-not-for-production-00001')
if APP_ENV == 'prod' and (len(SECRET_KEY) < 40 or SECRET_KEY.startswith('development')):
    raise ValueError('正式环境需要独立生成的 SECRET_KEY')
DEBUG = APP_ENV == 'dev'
ALLOWED_HOSTS = [x.strip() for x in setting('ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1],testserver').split(',') if x.strip()]
if APP_ENV == 'prod' and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    raise ValueError('正式环境必须指定服务器域名或 IP')
DATABASE_URL = setting('DATABASE_URL')
if DATABASE_URL:
    db = urlparse(DATABASE_URL)
    if db.scheme not in {'postgres', 'postgresql'}:
        raise ValueError('DATABASE_URL 只支持 PostgreSQL')
    db_name = unquote(db.path.lstrip('/'))
    if not db_name.startswith(f'labwork_{APP_ENV}'):
        raise ValueError('数据库名称必须与运行环境匹配')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': db_name,
        'USER': unquote(db.username or ''), 'PASSWORD': unquote(db.password or ''),
        'HOST': db.hostname or '127.0.0.1', 'PORT': db.port or 5432, 'CONN_MAX_AGE': 60}}
elif APP_ENV == 'prod':
    raise ValueError('正式环境必须配置 PostgreSQL；不自动降级到 SQLite')
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': RUNTIME_DIR / 'test.sqlite3'}}

WRITEBACK_ENABLED = str(setting('WRITEBACK_ENABLED', 'false')).lower() == 'true'
ADMIN_USERNAME = setting('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = setting('ADMIN_PASSWORD')
WEB_BIND = setting('WEB_BIND', '127.0.0.1')
WEB_PORT = int(setting('WEB_PORT', '18801'))
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'records']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
    'records.middleware.AccessMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'labwork.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages',
        'records.context.environment']}}]
WSGI_APPLICATION = 'labwork.wsgi.application'
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
HTTPS_ENABLED = str(setting('HTTPS_ENABLED', 'false')).lower() == 'true'
SESSION_COOKIE_SECURE = HTTPS_ENABLED
CSRF_COOKIE_SECURE = HTTPS_ENABLED
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if HTTPS_ENABLED else None
CSRF_TRUSTED_ORIGINS = [x for x in setting('CSRF_TRUSTED_ORIGINS').split(',') if x]
ALLOWED_NETWORKS = setting('ALLOWED_NETWORKS', '127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7')
TRUSTED_PROXIES = setting('TRUSTED_PROXIES', '127.0.0.1/32,::1/128')
DATA_UPLOAD_MAX_MEMORY_SIZE = 21 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
SESSION_COOKIE_AGE = 8 * 60 * 60
