from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, g
from google.cloud import firestore as fs

from api.firebase_config import get_db
from api.middleware.auth import require_auth, require_analyze_history

community_bp = Blueprint("community_bp", __name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50


# ───────────────────────────────────────────
# Service helpers
# ───────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _doc_to_post(doc) -> dict:
    data = doc.to_dict()
    data["id"] = doc.id
    # Firestore Timestamp → ISO string
    for key in ("created_at", "updated_at"):
        if key in data and hasattr(data[key], "isoformat"):
            data[key] = data[key].isoformat()
    return data


def _doc_to_comment(doc) -> dict:
    data = doc.to_dict()
    data["id"] = doc.id
    if "created_at" in data and hasattr(data["created_at"], "isoformat"):
        data["created_at"] = data["created_at"].isoformat()
    return data


def _get_post_or_404(post_id: str):
    db = get_db()
    doc = db.collection("posts").document(post_id).get()
    if not doc.exists:
        return None, jsonify({"error": "게시글을 찾을 수 없습니다.", "code": "NOT_FOUND"}), 404
    return doc, None, None


# ───────────────────────────────────────────
# Posts
# ───────────────────────────────────────────

@community_bp.route("/api/posts", methods=["GET"])
def list_posts():
    """
    게시글 목록 조회 (공개)
    ---
    tags:
      - Community
    parameters:
      - name: limit
        in: query
        type: integer
        default: 20
      - name: last_id
        in: query
        type: string
        description: 다음 페이지 커서 (이전 응답의 last_id 값)
    responses:
      200:
        description: 게시글 목록
    """
    try:
        db = get_db()
        limit = min(int(request.args.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
        last_id = request.args.get("last_id")

        query = (
            db.collection("posts")
            .order_by("created_at", direction=fs.Query.DESCENDING)
            .limit(limit + 1)
        )

        if last_id:
            last_doc = db.collection("posts").document(last_id).get()
            if last_doc.exists:
                query = query.start_after(last_doc)

        docs = list(query.stream())
        has_more = len(docs) > limit
        docs = docs[:limit]

        posts = [_doc_to_post(d) for d in docs]
        next_cursor = docs[-1].id if has_more and docs else None

        return jsonify({"posts": posts, "has_more": has_more, "last_id": next_cursor})

    except Exception as e:
        return jsonify({"error": f"게시글 목록 조회 실패: {str(e)}"}), 500


@community_bp.route("/api/posts", methods=["POST"])
@require_auth
@require_analyze_history
def create_post():
    """
    게시글 작성 (로그인 + 분석 이력 1회 이상 필수)
    ---
    tags:
      - Community
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
        description: "Bearer {Firebase ID Token}"
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - title
            - content
          properties:
            title:
              type: string
            content:
              type: string
            analyze_id:
              type: string
              description: 연결할 분석 결과 ID (선택)
            danger_level:
              type: string
              description: 분석 결과의 위험 등급 (선택)
            is_anonymous:
              type: boolean
              default: false
    responses:
      201:
        description: 생성된 게시글
      400:
        description: 필수 파라미터 누락
      401:
        description: 인증 실패
      403:
        description: 분석 이력 없음
    """
    try:
        body = request.get_json(silent=True) or {}
        title = (body.get("title") or "").strip()
        content = (body.get("content") or "").strip()

        if not title or not content:
            return jsonify({"error": "제목과 내용을 입력해주세요.", "code": "MISSING_FIELDS"}), 400
        if len(title) > 100:
            return jsonify({"error": "제목은 100자 이내로 작성해주세요.", "code": "TITLE_TOO_LONG"}), 400
        if len(content) > 5000:
            return jsonify({"error": "내용은 5000자 이내로 작성해주세요.", "code": "CONTENT_TOO_LONG"}), 400

        is_anonymous = bool(body.get("is_anonymous", False))
        db = get_db()
        now = _now()

        post_data = {
            "uid": g.uid,
            "display_name": "익명" if is_anonymous else g.user_name,
            "photo_url": "" if is_anonymous else g.user_picture,
            "title": title,
            "content": content,
            "analyze_id": body.get("analyze_id") or None,
            "danger_level": body.get("danger_level") or None,
            "is_anonymous": is_anonymous,
            "comment_count": 0,
            "created_at": now,
            "updated_at": now,
        }

        doc_ref = db.collection("posts").document()
        doc_ref.set(post_data)

        post_data["id"] = doc_ref.id
        post_data["created_at"] = now.isoformat()
        post_data["updated_at"] = now.isoformat()

        return jsonify(post_data), 201

    except Exception as e:
        return jsonify({"error": f"게시글 작성 실패: {str(e)}"}), 500


@community_bp.route("/api/posts/<post_id>", methods=["GET"])
def get_post(post_id: str):
    """
    게시글 상세 조회 (공개)
    ---
    tags:
      - Community
    parameters:
      - name: post_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: 게시글 상세
      404:
        description: 게시글 없음
    """
    try:
        doc, err_response, status = _get_post_or_404(post_id)
        if err_response:
            return err_response, status
        return jsonify(_doc_to_post(doc))

    except Exception as e:
        return jsonify({"error": f"게시글 조회 실패: {str(e)}"}), 500


@community_bp.route("/api/posts/<post_id>", methods=["DELETE"])
@require_auth
def delete_post(post_id: str):
    """
    게시글 삭제 (본인만 가능)
    ---
    tags:
      - Community
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
      - name: post_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: 삭제 완료
      403:
        description: 권한 없음
      404:
        description: 게시글 없음
    """
    try:
        doc, err_response, status = _get_post_or_404(post_id)
        if err_response:
            return err_response, status

        post = doc.to_dict()
        if post["uid"] != g.uid:
            return jsonify({"error": "본인의 게시글만 삭제할 수 있습니다.", "code": "FORBIDDEN"}), 403

        db = get_db()
        # 댓글 일괄 삭제 후 게시글 삭제
        comments = db.collection("comments").where("post_id", "==", post_id).stream()
        batch = db.batch()
        for c in comments:
            batch.delete(c.reference)
        batch.delete(doc.reference)
        batch.commit()

        return jsonify({"message": "게시글이 삭제되었습니다."})

    except Exception as e:
        return jsonify({"error": f"게시글 삭제 실패: {str(e)}"}), 500


# ───────────────────────────────────────────
# Comments
# ───────────────────────────────────────────

@community_bp.route("/api/posts/<post_id>/comments", methods=["GET"])
def list_comments(post_id: str):
    """
    댓글 목록 조회 (공개)
    ---
    tags:
      - Community
    parameters:
      - name: post_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: 댓글 목록
      404:
        description: 게시글 없음
    """
    try:
        db = get_db()
        if not db.collection("posts").document(post_id).get().exists:
            return jsonify({"error": "게시글을 찾을 수 없습니다.", "code": "NOT_FOUND"}), 404

        docs = (
            db.collection("comments")
            .where("post_id", "==", post_id)
            .order_by("created_at")
            .stream()
        )
        return jsonify({"comments": [_doc_to_comment(d) for d in docs]})

    except Exception as e:
        return jsonify({"error": f"댓글 조회 실패: {str(e)}"}), 500


@community_bp.route("/api/posts/<post_id>/comments", methods=["POST"])
@require_auth
def create_comment(post_id: str):
    """
    댓글 작성 (로그인 필수)
    ---
    tags:
      - Community
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
      - name: post_id
        in: path
        type: string
        required: true
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - content
          properties:
            content:
              type: string
    responses:
      201:
        description: 생성된 댓글
      400:
        description: 내용 누락
      401:
        description: 인증 실패
      404:
        description: 게시글 없음
    """
    try:
        db = get_db()
        post_ref = db.collection("posts").document(post_id)
        if not post_ref.get().exists:
            return jsonify({"error": "게시글을 찾을 수 없습니다.", "code": "NOT_FOUND"}), 404

        body = request.get_json(silent=True) or {}
        content = (body.get("content") or "").strip()
        if not content:
            return jsonify({"error": "댓글 내용을 입력해주세요.", "code": "MISSING_FIELDS"}), 400
        if len(content) > 1000:
            return jsonify({"error": "댓글은 1000자 이내로 작성해주세요.", "code": "CONTENT_TOO_LONG"}), 400

        now = _now()
        comment_data = {
            "post_id": post_id,
            "uid": g.uid,
            "display_name": g.user_name,
            "photo_url": g.user_picture,
            "content": content,
            "created_at": now,
        }

        comment_ref = db.collection("comments").document()
        # 댓글 저장 + 게시글 comment_count 증가 (배치)
        batch = db.batch()
        batch.set(comment_ref, comment_data)
        batch.update(post_ref, {"comment_count": fs.Increment(1)})
        batch.commit()

        comment_data["id"] = comment_ref.id
        comment_data["created_at"] = now.isoformat()

        return jsonify(comment_data), 201

    except Exception as e:
        return jsonify({"error": f"댓글 작성 실패: {str(e)}"}), 500


@community_bp.route("/api/posts/<post_id>/comments/<comment_id>", methods=["DELETE"])
@require_auth
def delete_comment(post_id: str, comment_id: str):
    """
    댓글 삭제 (본인만 가능)
    ---
    tags:
      - Community
    parameters:
      - name: Authorization
        in: header
        type: string
        required: true
      - name: post_id
        in: path
        type: string
        required: true
      - name: comment_id
        in: path
        type: string
        required: true
    responses:
      200:
        description: 삭제 완료
      403:
        description: 권한 없음
      404:
        description: 댓글 없음
    """
    try:
        db = get_db()
        comment_ref = db.collection("comments").document(comment_id)
        comment_doc = comment_ref.get()

        if not comment_doc.exists:
            return jsonify({"error": "댓글을 찾을 수 없습니다.", "code": "NOT_FOUND"}), 404

        comment = comment_doc.to_dict()
        if comment["uid"] != g.uid:
            return jsonify({"error": "본인의 댓글만 삭제할 수 있습니다.", "code": "FORBIDDEN"}), 403
        if comment["post_id"] != post_id:
            return jsonify({"error": "잘못된 게시글 ID입니다.", "code": "BAD_REQUEST"}), 400

        post_ref = db.collection("posts").document(post_id)
        batch = db.batch()
        batch.delete(comment_ref)
        batch.update(post_ref, {"comment_count": fs.Increment(-1)})
        batch.commit()

        return jsonify({"message": "댓글이 삭제되었습니다."})

    except Exception as e:
        return jsonify({"error": f"댓글 삭제 실패: {str(e)}"}), 500
