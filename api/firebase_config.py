import os
import json
import firebase_admin
from firebase_admin import credentials, firestore, auth as fb_auth

_app = None


def _init() -> firebase_admin.App:
    global _app
    if _app:
        return _app

    json_str = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    key_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH")

    if json_str:
        cred = credentials.Certificate(json.loads(json_str))
    elif key_path:
        cred = credentials.Certificate(key_path)
    else:
        raise RuntimeError(
            "Firebase 인증 정보가 없습니다. "
            "FIREBASE_SERVICE_ACCOUNT_JSON 또는 FIREBASE_SERVICE_ACCOUNT_PATH 환경변수를 설정하세요."
        )

    _app = firebase_admin.initialize_app(cred)
    return _app


def get_db() -> firestore.Client:
    _init()
    return firestore.client()


def verify_id_token(token: str) -> dict:
    _init()
    return fb_auth.verify_id_token(token)
