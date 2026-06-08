from functools import wraps
from flask import request, jsonify, g
from api.firebase_config import get_db, verify_id_token


def require_auth(f):
    """Firebase ID 토큰 검증 — Authorization: Bearer <token> 헤더 필수."""
    @wraps(f)
    def decorated(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({
                "error": "인증이 필요합니다. Authorization 헤더에 Bearer 토큰을 포함해주세요.",
                "code": "UNAUTHORIZED"
            }), 401

        token = header[7:]
        try:
            decoded = verify_id_token(token)
        except Exception:
            return jsonify({
                "error": "유효하지 않거나 만료된 인증 토큰입니다.",
                "code": "INVALID_TOKEN"
            }), 401

        g.uid = decoded["uid"]
        g.user_name = decoded.get("name", "알 수 없음")
        g.user_email = decoded.get("email", "")
        g.user_picture = decoded.get("picture", "")
        return f(*args, **kwargs)

    return decorated


def require_analyze_history(f):
    """분석 이력 1회 이상 존재 검증 — require_auth 이후에 적용해야 합니다."""
    @wraps(f)
    def decorated(*args, **kwargs):
        db = get_db()
        docs = (
            db.collection("analyze_history")
            .where("uid", "==", g.uid)
            .limit(1)
            .stream()
        )
        if not any(True for _ in docs):
            return jsonify({
                "error": "글을 쓰려면 먼저 '망할연' 분석 기능을 1회 이상 이용해야 합니다.",
                "code": "ANALYZE_REQUIRED"
            }), 403

        return f(*args, **kwargs)

    return decorated
