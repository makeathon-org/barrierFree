"""Firebase Cloud Functions (Python, Gen2) 엔트리.

FastAPI(ASGI) 앱 전체를 하나의 https 함수로 서빙한다.
a2wsgi 로 ASGI→WSGI 변환 후 Werkzeug Response.from_app 으로 디스패치.

무거운 import(osmnx 등)는 함수 본문에서 지연 로드해, firebase CLI 의
로컬 함수 분석(venv) 단계에서는 firebase_functions 만 있으면 되도록 한다.
"""

from firebase_functions import https_fn, options
from firebase_functions.params import SecretParam

# Gemini API 키는 Secret Manager 로 주입 (파일에 굽지 않음)
GEMINI_API_KEY = SecretParam("GEMINI_API_KEY")

options.set_global_options(
    region="asia-northeast3",
    memory=options.MemoryOption.GB_1,
    timeout_sec=300,
)

_wsgi = None


def _get_wsgi():
    global _wsgi
    if _wsgi is None:
        from a2wsgi import ASGIMiddleware
        from server import app  # FastAPI 앱

        _wsgi = ASGIMiddleware(app)
    return _wsgi


@https_fn.on_request(secrets=[GEMINI_API_KEY])
def api(req: https_fn.Request) -> https_fn.Response:
    return https_fn.Response.from_app(_get_wsgi(), req.environ)
