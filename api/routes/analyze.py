import json
import re
import pandas as pd
from flask import Blueprint, request, jsonify
from api.ai import chat, invoke_with_retry

analyze_bp = Blueprint('analyze_bp', __name__)

@analyze_bp.route('/api/analyze', methods=['POST'])
def analyze_chat():
    """
    카톡 대화 분석 API
    ---
    tags:
      - Analysis
    parameters:
      - name: target_name
        in: formData
        type: string
        required: true
        description: 분석 대상자 이름
      - name: file
        in: formData
        type: file
        required: true
        description: 카카오톡 CSV 또는 TXT 파일
      - name: user_type
        in: formData
        type: string
        required: false
        description: 사용자 연애유형 JSON 문자열 (퀴즈 건너뛴 경우 생략 가능)
    responses:
      200:
        description: 분석 결과
      400:
        description: 파라미터 누락
      500:
        description: 서버 오류
    """
    try:
        target_name = request.form.get('target_name')
        if not target_name:
            return jsonify({"error": "분석 대상자 이름을 입력해주세요."}), 400

        if 'file' not in request.files:
            return jsonify({"error": "CSV 또는 TXT 파일을 업로드해주세요."}), 400

        file = request.files['file']
        filename = file.filename.lower()

        if filename.endswith('.csv'):
            try:
                df = pd.read_csv(file, encoding='utf-8-sig')
            except:
                file.seek(0)
                df = pd.read_csv(file, encoding='cp949')
            chat_log = df.tail(800).to_string()
        elif filename.endswith('.txt'):
            try:
                content = file.read().decode('utf-8')
            except:
                file.seek(0)
                content = file.read().decode('cp949', errors='ignore')
            lines = content.splitlines()
            chat_log = '\n'.join(lines[-800:])
        else:
            return jsonify({"error": "지원하지 않는 파일 형식입니다. CSV 또는 TXT 파일만 가능합니다."}), 400

        # user_type 파싱 (optional)
        user_type = None
        user_type_raw = request.form.get('user_type')
        if user_type_raw:
            try:
                user_type = json.loads(user_type_raw)
            except:
                user_type = None

        # 프롬프트 유형 컨텍스트 분기
        if user_type:
            user_type_context = f"""
사용자 연애유형: {user_type.get('type_name')} - {user_type.get('label')}
유형 설명: {user_type.get('description')}
유형 약점: {', '.join(user_type.get('weaknesses', []))}

compatibility_issues는 위 유형과 상대 행동 패턴의 충돌 지점을 구체적으로 작성하라.
"""
            user_type_name = user_type.get('type_name')
        else:
            user_type_context = """
사용자 연애유형: 미검사
compatibility_issues는 대화 패턴만으로 일반적인 관계 충돌 지점을 분석하라.
"""
            user_type_name = "미검사"

        prompt = f"""
역할: 연애 상담 전문가. 카톡 대화를 분석하여 '{target_name}'의 유해한 행동 패턴을 분석하라.

- `likability_score`는 해당 행동이 관계를 얼마나 망치는지 나타내는 치명도 점수입니다. 가장 최악이고 치명적인 행동일수록 -100점에 가깝게 평가하세요 (0부터 -100 사이의 점수 부여).

{user_type_context}

분석 대상 대화:
{chat_log}

출력 형식 (JSON만 반환, 마크다운 금지):
{{
  "receipt_info": {{ "service_name": "망할연", "target_name": "{target_name}" }},
  "user_type": "{user_type_name}",
  "analysis_items": [
    {{ "behavior": "행동 명칭", "count": 0, "likability_score": 0, "description": "요약", "evidence": "인용구" }}
  ],
  "compatibility_issues": [
    {{ "issue": "충돌 원인", "severity": "높음/중간/낮음", "detail": "왜 안 맞는지 설명" }}
  ],
  "final_verdict": {{ "status": "판정", "comment": "한줄평" }}
}}
"""

        result = invoke_with_retry(prompt)
        if isinstance(result.content, list):
            content = "".join([part.get("text", "") if isinstance(part, dict) else str(part) for part in result.content])
        else:
            content = str(result.content)

        content = content.strip()
        json_match = re.search(r'\{.*\}', content, re.DOTALL)

        if json_match:
            json_str = json_match.group().replace('\n', '')
            analysis_data = json.loads(json_str)
            return jsonify(analysis_data)
        else:
            return jsonify({"error": "JSON 형식을 찾을 수 없습니다.", "raw": content}), 500

    except Exception as e:
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500
