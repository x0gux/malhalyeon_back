import json
import re
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, request, jsonify
# langchain/모델 객체 로딩은 무겁다 — import 시점에 끌어오면 AI를 안 쓰는 다른 API의
# 콜드스타트까지 그 비용을 떠안는다. 실제 호출 시점까지 미루는 지연 래퍼로 감싼다.
def invoke_simulation(*args, **kwargs):
    from api.ai import invoke_simulation as _f
    return _f(*args, **kwargs)


def invoke_simulation_fallback(*args, **kwargs):
    from api.ai import invoke_simulation_fallback as _f
    return _f(*args, **kwargs)


def invoke_auxiliary(*args, **kwargs):
    from api.ai import invoke_auxiliary as _f
    return _f(*args, **kwargs)

simulate_bp = Blueprint('simulate_bp', __name__)


def extract_text(result) -> str:
    """Safely extract plain text from a LangChain LLM result."""
    content = result.content if hasattr(result, 'content') else result
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get('text', ''))
            else:
                parts.append(str(block))
        return ''.join(parts).strip()
    return str(content).strip()


def build_system_prompt(analysis_items: list, danger_level: str) -> str:
    behavior_blocks = []
    for item in analysis_items:
        line = f"  - {item['behavior']} (빈도: {item['count']}회, 위험도: {item['likability_score']})"
        if item.get('description'):
            line += f"\n    ({item['description']})"
        behavior_blocks.append(line)
    behaviors = "\n".join(behavior_blocks)

    evidence_parts = []
    for item in analysis_items:
        ev = item.get('evidence')
        if ev:
            evidence_parts.append(f"[{item['behavior']}]\n{ev}")
    evidence_section = "\n\n".join(evidence_parts) if evidence_parts else "없음"

    return f"""너는 연애 대응 훈련 시뮬레이터의 '상대방' AI다.

[위험 행동 패턴]
{behaviors}
[위험 등급] {danger_level}

[실제 분석된 대화 샘플 — 이 사람의 말투·어조·문체를 정확히 모방하라]
{evidence_section}

[규칙]
- 위 대화 샘플에서 '상대방'의 말투, 어조, 문장 길이, 표현 방식을 그대로 따라 하라.
- 위 패턴에 나타난 행동을 자연스럽게 대화에 녹여라.
- 위험 등급 높을수록 집착·통제적 언어.
- 카카오톡 말투, 1~3문장, 이모티콘 금지(ㅋㅋ/ㅠㅠ 허용), 이름 언급 금지.
- 너='상대방', 사용자='본인'.
- 올바른 대응 → 잠시 누그러지다 패턴 반복. 잘못된 대응 → 패턴 강화."""


def build_choices_prompt(conversation_history: list, analysis_items: list) -> str:
    history_text = "\n".join(
        f"{'본인' if m['role'] == 'user' else '상대방'}: {m['content']}"
        for m in conversation_history[-4:]
    )
    behaviors = ", ".join(item['behavior'] for item in analysis_items)
    return f"""[위험 패턴] {behaviors}
[최근 대화]
{history_text}

상대방 마지막 말에 대한 대응 선택지 4개 생성.
1:경계 설정(권장) 2:달래기 3:감정적 반응 4:무시/전환
카카오톡 말투, 이름 금지.

출력 (JSON만, 마크다운 금지):
{{"choices":[{{"id":1,"text":"...","strategy":"경계 설정","recommended":true}},{{"id":2,"text":"...","strategy":"달래기","recommended":false}},{{"id":3,"text":"...","strategy":"감정적 반응","recommended":false}},{{"id":4,"text":"...","strategy":"무시/전환","recommended":false}}]}}"""


def build_feedback_prompt(user_choice: str, opponent_response: str, analysis_items: list) -> str:
    behaviors = ", ".join(item['behavior'] for item in analysis_items)
    return f"""[위험 패턴] {behaviors}
[본인 대응] {user_choice}
[상대방 반응] {opponent_response}

대응 평가 (JSON만, 마크다운 금지):
{{"score":0,"label":"잘했어요/아쉬워요/위험해요 중 하나","feedback":"30자 이내","tip":"30자 이내 또는 null"}}

score: 경계 설정=80~100, 달래기=40~60, 감정적 반응=10~30, 무시/전환=50~70"""


def _parse_choices(raw: str) -> dict:
    data = {"choices": []}
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return data


def _parse_feedback(raw: str):
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


_FALLBACK_OPENING = "오늘 뭐했어? 연락이 좀 뜸한 것 같아서."
_FALLBACK_REPLY = "왜 말이 없어? 무슨 일 있는 거야?"


def _opponent_message(prompt: str, fallback: str) -> str:
    """상대방 메시지 생성.

    1) gemma로 생성, 빈 응답이면 한 번 더 시도.
    2) gemma가 예외(429/503 소진)나 빈 응답으로 실패하면 빠른 gemini로 1회 폴백.
    3) 그래도 비면 고정 문구.
    gemma 호출이 예외를 던져도 시뮬레이션 전체가 500으로 죽지 않게 한다."""
    try:
        for _ in range(2):
            text = extract_text(invoke_simulation(prompt))
            if text:
                return text
    except Exception as e:
        print(f"상대방(gemma) 응답 실패 — gemini 폴백: {e}")

    try:
        text = extract_text(invoke_simulation_fallback(prompt))
        if text:
            return text
    except Exception as e:
        print(f"상대방 gemini 폴백도 실패 — 고정 문구 사용: {e}")

    return fallback


def _invoke_and_parse_choices(prompt: str) -> dict:
    # 선택지는 JSON 구조화 출력 — 말투 모방이 불필요하므로 빠른 보조 모델 사용.
    return _parse_choices(extract_text(invoke_auxiliary(prompt)))


def _invoke_and_parse_feedback(prompt: str):
    # 피드백도 JSON 구조화 출력 — 빠른 보조 모델 사용.
    return _parse_feedback(extract_text(invoke_auxiliary(prompt)))


# ───────────────────────────────────────────
# API 엔드포인트
# ───────────────────────────────────────────

@simulate_bp.route('/api/simulate/start', methods=['POST'])
def start_simulation():
    """
    시뮬레이션 시작 — 첫 번째 상대방 메시지 및 초기 선택지 병렬 생성
    ---
    tags:
      - Simulate
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - analysis_items
          properties:
            analysis_items:
              type: array
              description: 분석 결과 항목 리스트
              example: [{"behavior": "위치 집착", "count": 5, "likability_score": -40}]
            danger_level:
              type: string
              description: 위험 등급 (안전/주의/경고/위험)
              example: "경고"
    responses:
      200:
        description: 첫 번째 상대방 메시지, 초기 선택지, 턴 번호
        schema:
          type: object
          properties:
            message:
              type: string
            choices:
              type: array
            turn:
              type: integer
      400:
        description: analysis_items 누락
      500:
        description: 서버 오류
    """
    try:
        body = request.get_json()
        analysis_items = body.get('analysis_items', [])
        danger_level = body.get('danger_level', '주의')

        if not analysis_items:
            return jsonify({"error": "analysis_items가 필요합니다."}), 400

        system_prompt = build_system_prompt(analysis_items, danger_level)

        opening_prompt = f"""{system_prompt}

지금 대화를 시작하라. 일상적인 말투로 첫 메시지를 보내되 위험 패턴이 은근히 느껴지게. 1~2문장."""

        behaviors = ", ".join(item['behavior'] for item in analysis_items)
        initial_choices_prompt = f"""[위험 패턴] {behaviors}

이 위험 패턴을 가진 상대방이 막 대화를 시작했다.
상대방의 첫 메시지에 대응할 선택지 4개 생성.
1:경계 설정(권장) 2:달래기 3:감정적 반응 4:무시/전환
카카오톡 말투, 이름 금지.

출력 (JSON만, 마크다운 금지):
{{"choices":[{{"id":1,"text":"...","strategy":"경계 설정","recommended":true}},{{"id":2,"text":"...","strategy":"달래기","recommended":false}},{{"id":3,"text":"...","strategy":"감정적 반응","recommended":false}},{{"id":4,"text":"...","strategy":"무시/전환","recommended":false}}]}}"""

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_msg = executor.submit(_opponent_message, opening_prompt, _FALLBACK_OPENING)
            future_choices = executor.submit(_invoke_and_parse_choices, initial_choices_prompt)

            opponent_message = future_msg.result()
            choices_data = future_choices.result()

        return jsonify({
            "message": opponent_message,
            "choices": choices_data.get("choices", []),
            "turn": 1
        })

    except Exception as e:
        return jsonify({"error": f"시뮬레이션 시작 실패: {str(e)}"}), 500


@simulate_bp.route('/api/simulate/reply', methods=['POST'])
def simulate_reply():
    """
    상대방 응답 + 선택지 + 피드백 반환
    ---
    tags:
      - Simulate
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - user_message
          properties:
            analysis_items:
              type: array
              description: 분석 결과 항목 리스트
              example: [{"behavior": "위치 집착", "count": 5, "likability_score": -40}]
            danger_level:
              type: string
              description: 위험 등급
              example: "경고"
            conversation_history:
              type: array
              description: 지금까지의 대화 히스토리 [{role, content}]
              example: [{"role": "assistant", "content": "오늘 뭐해?"}]
            user_message:
              type: string
              description: 본인의 메시지
              example: "그냥 집에 있어"
    responses:
      200:
        description: 상대방 응답, 다음 선택지, 직전 피드백
        schema:
          type: object
          properties:
            opponent_message:
              type: string
            choices:
              type: array
            feedback:
              type: object
            turn:
              type: integer
      400:
        description: user_message 누락
      500:
        description: 서버 오류
    """
    try:
        body = request.get_json()
        analysis_items = body.get('analysis_items', [])
        danger_level = body.get('danger_level', '주의')
        history = body.get('conversation_history', [])
        user_message = body.get('user_message', '')

        if not user_message:
            return jsonify({"error": "user_message가 필요합니다."}), 400

        # 하위 호환: 첫 턴 선택지 요청 (start에서 이미 반환하나 프론트 구버전 대응)
        if user_message == '시뮬레이션 시작':
            opponent_message = history[0]['content'] if history else "오늘 뭐해?"
            choices_prompt = build_choices_prompt(history, analysis_items)
            choices_data = _invoke_and_parse_choices(choices_prompt)
            return jsonify({
                "opponent_message": opponent_message,
                "choices": choices_data.get("choices", []),
                "feedback": None,
                "turn": 1
            })

        # 1. 시스템 프롬프트 한 번 생성 후 재사용
        system_prompt = build_system_prompt(analysis_items, danger_level)
        history_text = "\n".join(
            f"{'본인' if m['role'] == 'user' else '상대방'}: {m['content']}"
            for m in history
        )

        reply_prompt = f"""{system_prompt}

[지금까지 대화]
{history_text}
본인: {user_message}

위 대화에 이어 상대방으로서 답하라. 1~3문장."""

        # 2. 상대방 응답 생성 (선택지/피드백보다 먼저 필요)
        opponent_message = _opponent_message(reply_prompt, _FALLBACK_REPLY)

        # 3. 선택지와 피드백을 병렬 생성
        updated_history = history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": opponent_message}
        ]
        choices_prompt = build_choices_prompt(updated_history, analysis_items)

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_choices = executor.submit(_invoke_and_parse_choices, choices_prompt)
            future_feedback = (
                executor.submit(
                    _invoke_and_parse_feedback,
                    build_feedback_prompt(user_message, opponent_message, analysis_items)
                )
                if history else None
            )

            choices_data = future_choices.result()
            feedback_data = future_feedback.result() if future_feedback else None

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
    ---
    tags:
      - Simulate
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - conversation_history
          properties:
            analysis_items:
              type: array
              description: 분석 결과 항목 리스트
              example: [{"behavior": "위치 집착", "count": 5, "likability_score": -40}]
            conversation_history:
              type: array
              description: 전체 대화 히스토리
              example: [{"role": "assistant", "content": "오늘 뭐해?"}, {"role": "user", "content": "집이야"}]
            score_history:
              type: array
              description: 각 턴별 대응 점수 리스트
              example: [85, 45, 60]
    responses:
      200:
        description: 훈련 총평 결과
        schema:
          type: object
          properties:
            total_score:
              type: integer
            grade:
              type: string
            title:
              type: string
            summary:
              type: string
            best_response:
              type: string
            worst_response:
              type: string
            advice:
              type: string
      400:
        description: conversation_history 누락
      500:
        description: 서버 오류
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
        raw = extract_text(result)

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
