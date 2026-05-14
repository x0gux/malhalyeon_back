import json
import re
import pandas as pd
from flask import Blueprint, request, jsonify
from api.ai import chat, invoke_with_retry

analyze_bp = Blueprint('analyze_bp', __name__)

# ─────────────────────────────────────────────────────────────────
# 2024년 여성가족부 여성폭력 실태조사 기반 위험 행동 분류 체계
# 출처: 한국여성정책연구원 「2024년 여성폭력 실태조사」(여성가족부, 2024.12)
# ─────────────────────────────────────────────────────────────────
VIOLENCE_TAXONOMY = """
[공공데이터 기반 위험 행동 분류 체계]
출처: 2024년 여성폭력 실태조사 (여성가족부 / 한국여성정책연구원)

■ 정서적 폭력 유형 (피해 경험률 17.8% / 교제관계 2.9%)
  - 면박주거나 모멸감을 느끼게 하는 행위 (피해자 62.8% 경험)
  - 비하적인 표현이나 욕설·폭언을 쏟아붓는 행위 (57.7%)
  - 고함을 치거나 물건을 던지는 등 겁주는 행위 (54.2%)
  - 때리겠다고 협박하여 위협감을 느끼게 하는 행위 (24.0%)
  - 아끼는 동물·물건·사람을 해치겠다고 위협하는 행위 (6.5%)
  - 자살 또는 자해를 암시하거나 실행하겠다고 위협하는 행위 (3.0%)

■ 통제 유형 (피해 경험률 5.2% / 교제관계 1.6%)
  - 어디에 있는지 지나치게 알려고 하는 행위 (피해자 69.4% 경험) ← 가장 빈번
  - 친구와 연락하거나 만나지 못하게 하는 행위 (53.4%)
  - 가족·친척과 연락하거나 만나지 못하게 하는 행위 (19.3%)
  - 외출을 막거나 집에 감금하는 행위 (14.6%)
  - 의료적 도움이 필요할 때 허락을 받도록 하는 행위 (4.4%)

■ 복합피해 특성 (통제 피해의 78.1%가 복합피해)
  - 통제 행위는 단독으로 발생하기보다 정서적 폭력·신체적 폭력과 동반되는 경향
  - 교제관계에서 통제 가해자의 24.7%가 '사귀고 있던 사람'

■ 카카오톡 대화에서 감지 가능한 위험 신호 패턴
  - 위치·행방 집착: "지금 어디야", "누구랑 있어", 지속적 위치 확인 요구
  - 관계 고립 시도: 친구·가족 만남 제한, 특정 관계 차단 요구
  - 정서적 폭언: 모욕적 표현, 욕설, 비하 발언
  - 협박성 언어: "헤어지면 어떻게 할지 몰라", 자해·자살 암시
  - 과도한 연락 통제: 즉각 답장 강요, 읽씹에 대한 과도한 반응
  - 감시·의심: 상대방 행동에 대한 지속적 의심, 거짓말 추궁
"""

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
        description: 카카오톡 CSV/TXT 또는 인스타그램 HTML 파일
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
            return jsonify({"error": "CSV, TXT 또는 HTML 파일을 업로드해주세요."}), 400

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

        elif filename.endswith('.html') or filename.endswith('.htm'):
            try:
                content = file.read().decode('utf-8')
            except:
                file.seek(0)
                content = file.read().decode('cp949', errors='ignore')
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')
            text = soup.get_text(separator='\n')
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            chat_log = '\n'.join(lines[-800:])

        else:
            return jsonify({"error": "지원하지 않는 파일 형식입니다. CSV, TXT 또는 HTML 파일만 가능합니다."}), 400

        # user_type 파싱 (optional)
        user_type = None
        user_type_raw = request.form.get('user_type')
        if user_type_raw:
            try:
                user_type = json.loads(user_type_raw)
            except:
                user_type = None

        # 사용자 유형 컨텍스트 분기
        if user_type:
            user_type_context = f"""
사용자 연애유형: {user_type.get('type_name')} - {user_type.get('label')}
유형 설명: {user_type.get('description')}
유형 약점: {', '.join(user_type.get('weaknesses', []))}

compatibility_issues는 위 유형과 상대 행동 패턴의 충돌 지점을 위의 공공데이터 분류 체계에 근거해 구체적으로 작성하라.
"""
            user_type_name = user_type.get('type_name')
        else:
            user_type_context = """
사용자 연애유형: 미검사
compatibility_issues는 대화 패턴만으로 일반적인 관계 충돌 지점을 위의 공공데이터 분류 체계에 근거해 분석하라.
"""
            user_type_name = "미검사"

        prompt = f"""
역할: 연애 상담 전문가. 아래 [공공데이터 기반 위험 행동 분류 체계]를 기준으로 '{target_name}'의 대화 패턴을 분석하라.

{VIOLENCE_TAXONOMY}

분석 기준:
- `likability_score`는 해당 행동이 관계를 망치는 치명도 점수 (0 ~ -100 사이).
  위의 분류 체계에서 더 심각한 유형일수록 -100에 가깝게 평가.
  예) 자해 협박(-90~-100) > 위치 집착(-40~-60) > 폭언(-30~-50) > 과도한 연락 통제(-20~-40)
- `danger_type`은 위 분류 체계 중 해당하는 유형명을 그대로 사용 (정서적 폭력 / 통제 / 복합피해 위험 / 해당없음)
- 분석 결과는 가장 핵심적이고 치명적인 원인 3~4개만 작성. 길면 안 됨.
- [절대 규칙 0] 분석 결과에 target_name을 포함시키지 마시오.
- [절대 규칙 1] `evidence`에는 반드시 제공된 대화에 실제로 존재하는 문장만 인용하라. 없는 내용 지어내지 말 것.
- [절대 규칙 2] 모든 분석 내용은 제공된 대화 사실에만 근거하라. 하지도 않은 말·행동을 임의로 추가 금지.
- [절대 규칙 3] `analysis_items`는 count 높은 순으로 정렬.
- [절대 규칙 4] `behavir` 은 대화내역을 인용하는것이아닌 행동을 지칭함. ex) 내가 잘못해줄거같아 -> 상대에게 책임 분가
{user_type_context}

분석 대상 대화:
{chat_log}

출력 형식 (JSON만 반환, 마크다운 금지):
{{
  "receipt_info": {{ "service_name": "망할연", "target_name": "{target_name}" }},
  "user_type": "{user_type_name}",
  "analysis_items": [
    {{
      "behavior": "행동 명칭",
      "danger_type": "정서적 폭력 / 통제 / 복합피해 위험 / 해당없음",
      "count": 0,
      "likability_score": 0,
      "description": "요약",
      "evidence": "인용구"
    }}
  ],
  "compatibility_issues": [
    {{ "issue": "충돌 원인", "severity": "높음/중간/낮음", "detail": "왜 안 맞는지 설명" }}
  ],
  "danger_level": "안전 / 주의 / 경고 / 위험",
  "danger_comment": "데이트폭력 위험 신호 관련 한줄 코멘트 (위험 신호 없으면 null)",
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
            json_str = json_match.group()
            try:
                analysis_data = json.loads(json_str, strict=False)
                return jsonify(analysis_data)
            except json.JSONDecodeError as decode_error:
                return jsonify({"error": f"JSON 파싱 오류: {str(decode_error)}", "raw": json_str}), 500
        else:
            return jsonify({"error": "JSON 형식을 찾을 수 없습니다.", "raw": content}), 500

    except Exception as e:
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500