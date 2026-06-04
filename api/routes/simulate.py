import json
import re
from flask import Blueprint, request, jsonify
from api.ai import invoke_simulation

simulate_bp = Blueprint('simulate_bp', __name__)

# ───────────────────────────────────────────
# 시뮬레이션 시스템 프롬프트 생성
# analysis_items의 위험 패턴을 기반으로 상대방 페르소나를 구성
# ───────────────────────────────────────────
def build_system_prompt(analysis_items: list, danger_level: str) -> str:
    behaviors = "\n".join(
        f"  - {item['behavior']} (빈도: {item['count']}회, 위험도: {item['likability_score']})"
        for item in analysis_items
    )

    return f"""
너는 연애 대응 훈련 시뮬레이터에서 '상대방' 역할을 맡은 AI다.
아래 분석된 실제 위험 행동 패턴을 바탕으로 상대방 캐릭터를 연기하라.

[상대방의 위험 행동 패턴]
{behaviors}

[전체 위험 등급]
{danger_level}

[연기 규칙]
- 반드시 위 패턴에서 나타난 행동만 자연스럽게 대화에 녹여라.
- 위험 등급이 높을수록 더 집착적·통제적 언어를 사용하라.
- 실제 연인 사이의 일상 대화처럼 자연스럽게 시작하다가 위험 패턴을 드러내라.
- 짧고 현실감 있는 카카오톡 말투로 대화하라. (이모티콘, ㅋㅋ, ㅠㅠ 등 자연스럽게 사용)
- 한 번에 1~3문장 이내로 짧게 답하라.
- 상대방 이름이나 본인 이름은 절대 언급하지 마라.
- 너는 '상대방'이고 사용자는 '본인'이다.
- 사용자가 올바른 대응을 하면 잠시 누그러지는 척하다가 다시 패턴을 반복하라.
- 사용자가 잘못된 대응을 하면 패턴을 더 강하게 드러내라.
"""

# ───────────────────────────────────────────
# 선택지 생성 프롬프트
# ───────────────────────────────────────────
def build_choices_prompt(conversation_history: list, analysis_items: list) -> str:
    history_text = "\n".join(
        f"{'본인' if m['role'] == 'user' else '상대방'}: {m['content']}"
        for m in conversation_history[-6:]  # 최근 6턴만 참고
    )

    behaviors = ", ".join(item['behavior'] for item in analysis_items)

    return f"""
아래 대화에서 상대방이 방금 한 말에 대해 본인이 선택할 수 있는 대응 선택지 4개를 만들어라.

[위험 행동 패턴]
{behaviors}

[최근 대화]
{history_text}

[선택지 규칙]
- 선택지는 반드시 4개.
- 각 선택지는 서로 다른 전략을 가져야 한다.
  1번: 건강한 경계 설정 (권장)
  2번: 부드럽게 달래는 대응 (관계 유지 시도)
  3번: 감정적으로 반응 (비권장)
  4번: 무시 또는 화제 전환
- 실제 카카오톡에서 보낼 법한 짧고 자연스러운 말투로 작성하라.
- 이름 언급 금지.

출력 형식 (JSON만 반환, 마크다운 금지):
{{
  "choices": [
    {{"id": 1, "text": "선택지 내용", "strategy": "경계 설정", "recommended": true}},
    {{"id": 2, "text": "선택지 내용", "strategy": "달래기", "recommended": false}},
    {{"id": 3, "text": "선택지 내용", "strategy": "감정적 반응", "recommended": false}},
    {{"id": 4, "text": "선택지 내용", "strategy": "무시/전환", "recommended": false}}
  ]
}}
"""

# ───────────────────────────────────────────
# 피드백 생성 프롬프트
# ───────────────────────────────────────────
def build_feedback_prompt(user_choice: str, opponent_response: str, analysis_items: list) -> str:
    behaviors = ", ".join(item['behavior'] for item in analysis_items)

    return f"""
연애 대응 훈련에서 본인이 아래와 같이 대응했다.

[상대방의 위험 행동 패턴]
{behaviors}

[본인의 대응]
{user_choice}

[상대방의 반응]
{opponent_response}

이 대응이 얼마나 적절했는지 평가하고 짧은 피드백을 제공하라.

출력 형식 (JSON만 반환, 마크다운 금지):
{{
  "score": 0,
  "label": "잘했어요 / 아쉬워요 / 위험해요",
  "feedback": "피드백 (30자 이내)",
  "tip": "더 나은 대응 팁 (30자 이내, 없으면 null)"
}}

score 기준:
- 경계를 명확히 설정: 80~100
- 부드럽게 달랬지만 패턴 강화: 40~60
- 감정적으로 반응해 상황 악화: 10~30
- 무시/전환으로 일시 회피: 50~70
"""


# ───────────────────────────────────────────
# API 엔드포인트
# ───────────────────────────────────────────

@simulate_bp.route('/api/simulate/start', methods=['POST'])
def start_simulation():
    """
    시뮬레이션 시작 — 첫 번째 상대방 메시지 생성
    Body: { analysis_items, danger_level }
    """
    try:
        body = request.get_json()
        analysis_items = body.get('analysis_items', [])
        danger_level = body.get('danger_level', '주의')

        if not analysis_items:
            return jsonify({"error": "analysis_items가 필요합니다."}), 400

        system_prompt = build_system_prompt(analysis_items, danger_level)

        opening_prompt = f"""
{system_prompt}

지금 대화를 시작하라. 일상적인 말투로 자연스럽게 첫 메시지를 보내되,
위험 패턴이 은근히 느껴지도록 시작하라. 1~2문장으로 짧게.
"""

        result = invoke_simulation(opening_prompt)
        content = result.content if hasattr(result, 'content') else str(result)

        return jsonify({
            "message": content.strip(),
            "turn": 1
        })

    except Exception as e:
        return jsonify({"error": f"시뮬레이션 시작 실패: {str(e)}"}), 500


@simulate_bp.route('/api/simulate/reply', methods=['POST'])
def simulate_reply():
    """
    상대방 응답 + 선택지 + 피드백 반환
    Body: {
        analysis_items,
        danger_level,
        conversation_history: [{role, content}],
        user_message: string
    }
    """
    try:
        body = request.get_json()
        analysis_items = body.get('analysis_items', [])
        danger_level = body.get('danger_level', '주의')
        history = body.get('conversation_history', [])
        user_message = body.get('user_message', '')

        if not user_message:
            return jsonify({"error": "user_message가 필요합니다."}), 400

        # 1. 상대방 응답 생성
        system_prompt = build_system_prompt(analysis_items, danger_level)
        history_text = "\n".join(
            f"{'본인' if m['role'] == 'user' else '상대방'}: {m['content']}"
            for m in history
        )

        reply_prompt = f"""
{system_prompt}

[지금까지 대화]
{history_text}
본인: {user_message}

위 대화에 이어서 상대방으로서 자연스럽게 답하라. 1~3문장 이내.
"""

        reply_result = invoke_simulation(reply_prompt)
        opponent_message = (reply_result.content if hasattr(reply_result, 'content') else str(reply_result)).strip()

        # 2. 다음 선택지 생성
        updated_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": opponent_message}
        ]
        choices_prompt = build_choices_prompt(updated_history, analysis_items)
        choices_result = invoke_simulation(choices_prompt)
        choices_raw = (choices_result.content if hasattr(choices_result, 'content') else str(choices_result)).strip()

        choices_data = {"choices": []}
        json_match = re.search(r'\{.*\}', choices_raw, re.DOTALL)
        if json_match:
            try:
                choices_data = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # 3. 직전 본인 대응에 대한 피드백 생성 (첫 턴 제외)
        feedback_data = None
        if history:
            feedback_prompt = build_feedback_prompt(user_message, opponent_message, analysis_items)
            feedback_result = invoke_simulation(feedback_prompt)
            feedback_raw = (feedback_result.content if hasattr(feedback_result, 'content') else str(feedback_result)).strip()
            fb_match = re.search(r'\{.*\}', feedback_raw, re.DOTALL)
            if fb_match:
                try:
                    feedback_data = json.loads(fb_match.group())
                except json.JSONDecodeError:
                    pass

        return jsonify({
            "opponent_message": opponent_message,
            "choices": choices_data.get("choices", []),
            "feedback": feedback_data,
            "turn": len(updated_history) // 2 + 1
        })

    except Exception as e:
        return jsonify({"error": f"시뮬레이션 응답 실패: {str(e)}"}), 500


@simulate_bp.route('/api/simulate/result', methods=['POST'])
def simulation_result():
    """
    시뮬레이션 종료 — 전체 대화 총평
    Body: { analysis_items, conversation_history, score_history }
    """
    try:
        body = request.get_json()
        analysis_items = body.get('analysis_items', [])
        history = body.get('conversation_history', [])
        score_history = body.get('score_history', [])

        if not history:
            return jsonify({"error": "conversation_history가 필요합니다."}), 400

        avg_score = sum(score_history) / len(score_history) if score_history else 0
        history_text = "\n".join(
            f"{'본인' if m['role'] == 'user' else '상대방'}: {m['content']}"
            for m in history
        )
        behaviors = ", ".join(item['behavior'] for item in analysis_items)

        result_prompt = f"""
아래는 연애 위험 상황 대응 훈련의 전체 대화 기록이다.

[상대방의 위험 행동 패턴]
{behaviors}

[전체 대화]
{history_text}

[평균 대응 점수]
{avg_score:.0f}점

이 훈련 결과를 총평하라.

출력 형식 (JSON만 반환, 마크다운 금지):
{{
  "total_score": {avg_score:.0f},
  "grade": "A / B / C / D 중 하나",
  "title": "결과 제목 (10자 이내)",
  "summary": "전체 대응 총평 (50자 이내)",
  "best_response": "가장 잘한 대응 한 줄",
  "worst_response": "가장 아쉬운 대응 한 줄",
  "advice": "앞으로의 조언 (40자 이내)"
}}
"""

        result = invoke_simulation(result_prompt)
        raw = (result.content if hasattr(result, 'content') else str(result)).strip()

        result_data = {}
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            try:
                result_data = json.loads(json_match.group())
            except json.JSONDecodeError:
                result_data = {"error": "결과 파싱 실패", "raw": raw}

        return jsonify(result_data)

    except Exception as e:
        return jsonify({"error": f"결과 생성 실패: {str(e)}"}), 500
