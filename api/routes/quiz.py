from flask import Blueprint, jsonify, request
from api.data import QUIZ_DATA, get_user_type

quiz_bp = Blueprint('quiz_bp', __name__)

@quiz_bp.route('/api/quiz', methods=['GET'])
def get_quiz():
    """
    연애유형 검사 문항 전체 조회
    ---
    tags:
      - Quiz
    responses:
      200:
        description: 전체 문항 리스트
    """
    return jsonify(QUIZ_DATA)

@quiz_bp.route('/api/quiz/<int:quiz_id>', methods=['GET'])
def get_quiz_by_id(quiz_id):
    """
    특정 문항 단건 조회
    ---
    tags:
      - Quiz
    parameters:
      - name: quiz_id
        in: path
        type: integer
        required: true
    responses:
      200:
        description: 해당 문항
      404:
        description: 문항 없음
    """
    item = next((q for q in QUIZ_DATA if q["id"] == quiz_id), None)
    if not item:
        return jsonify({"error": "해당 문항이 없습니다."}), 404
    return jsonify(item)

@quiz_bp.route('/api/quiz/submit', methods=['POST'])
def submit_quiz():
    """
    퀴즈 답변 제출 → 사용자 연애유형 반환
    ---
    tags:
      - Quiz
    parameters:
      - name: body
        in: body
        schema:
          type: object
          properties:
            answers:
              type: array
              example: [{"id": 1, "choice": "A"}, {"id": 2, "choice": "B"}]
    responses:
      200:
        description: 사용자 연애유형
      400:
        description: answers 누락
    """
    body = request.get_json()
    if not body or "answers" not in body:
        return jsonify({"error": "answers 필드가 필요합니다."}), 400

    answers = body.get("answers", [])
    score = sum(1 for a in answers if a.get("choice") == "A")
    user_type = get_user_type(score)

    return jsonify({
        "score": score,
        "user_type": user_type
    })
