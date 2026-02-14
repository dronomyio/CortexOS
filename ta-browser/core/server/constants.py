import os

APP_VERSION = "1.0.0"
APP_NAME = "Web Agent Web API"
API_PREFIX = "/api"
WEB_PREFIX = "/v1/web"
IS_DEBUG = False
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8080))
WORKERS = 1
GLOBAL_TIMEOUT = 3600 # 1 hour in seconds
GLOBAL_PREFIX="/api/v1"