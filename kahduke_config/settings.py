"""
Django settings for the KahDuke project.

Every value that differs between a laptop and the course VM is read from the
environment with django-environ. A fresh clone needs no .env at all: it runs
on SQLite with an in-memory channel layer. See .env.example.
"""

import warnings
from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, True),
    REDIS_SOCKET_TIMEOUT=(int, 10),
)
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env(
    "SECRET_KEY",
    default="django-insecure-local-only-change-me-in-dot-env",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Required behind Caddy (or any TLS-terminating proxy). Without it Django
# believes every request arrived over plain HTTP: CSRF failures on login and
# redirect loops.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# Application definition

INSTALLED_APPS = [
    # daphne must come before staticfiles so `runserver` serves ASGI.
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "allauth",
    "allauth.account",
    "accounts",
    "kwizzes",
    "games",
    "repos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "kahduke_config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "kahduke_config.wsgi.application"
ASGI_APPLICATION = "kahduke_config.asgi.application"


# Database
# No DATABASE_URL means SQLite on the laptop; Compose sets it on the server.

DATABASES = {"default": env.db(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Channels
# With REDIS_URL set (Compose, or a local `docker run redis:7`), use Redis.
# Without it, fall back to the in-memory layer, which is fine for a single
# `runserver` process but cannot span processes.

if env("REDIS_URL", default=""):
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [
                    {
                        "address": env("REDIS_URL"),
                        "socket_timeout": env.int("REDIS_SOCKET_TIMEOUT"),
                    }
                ],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }


# Authentication

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

ACCOUNT_LOGIN_METHODS = {"email", "username"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "username*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_LOGOUT_ON_GET = False
LOGIN_REDIRECT_URL = "kwizzes:dashboard"
ACCOUNT_LOGOUT_REDIRECT_URL = "home"


# Internationalization

LANGUAGE_CODE = "en-us"

TIME_ZONE = "America/New_York"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
# The Dockerfile runs collectstatic into here; WhiteNoise serves it.
STATIC_ROOT = BASE_DIR / "staticfiles"
# Locally there is no collectstatic run, so STATIC_ROOT may not exist yet.
warnings.filterwarnings("ignore", message="No directory at", module="whitenoise.base")


# Email
# Console backend: allauth's verification and reset emails print to the log.

MAILERS = {
    "default": {
        "BACKEND": env(
            "EMAIL_BACKEND",
            default="django.core.mail.backends.console.EmailBackend",
        ),
    },
}


# With DEBUG=False, Django sends 500 tracebacks only to mail_admins, so
# `docker compose logs` shows the request and no error. This prints them.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
