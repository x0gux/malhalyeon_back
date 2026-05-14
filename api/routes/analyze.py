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
            chat_format = "카카오톡 CSV 내보내기 형식. 열 구조: 날짜, 화자 이름, 메시지 내용 순. 화자는 이름 열로 구분됨."

        elif filename.endswith('.txt'):
            try:
                content = file.read().decode('utf-8')
            except:
                file.seek(0)
                content = file.read().decode('cp949', errors='ignore')
            lines = content.splitlines()
            chat_log = '\n'.join(lines[-800:])
            chat_format = "카카오톡 TXT 내보내기 형식. 각 줄은 '[날짜] [이름] : [메시지]' 구조. 화자는 콜론(:) 앞 이름으로 구분됨."

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
            chat_format = "인스타그램 HTML 내보내기 형식. 화자 이름이 메시지 앞에 표시됨."

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
역할: 데이트폭력 피해 상담 경력 10년의 전문가.
감정적 판단 없이 대화 증거에만 근거해 냉정하게 분석한다.
아래 [공공데이터 기반 위험 행동 분류 체계]를 기준으로 '{target_name}'의 대화 패턴을 분석하라.

{VIOLENCE_TAXONOMY}

[대화 형식 안내]
{chat_format}
분석 대상자: '{target_name}' — 이 사람의 발화만 분석 대상으로 삼아라.

{user_type_context}

분석 대상 대화:
{chat_log}

─────────────────────────────────────────
[분석 규칙 - 아래 규칙을 모두 지켜서 JSON을 생성하라]
─────────────────────────────────────────

[규칙 1] 출력 대상 및 개수
- 분석 결과에 target_name을 절대 포함시키지 마라.
- `analysis_items`는 반드시 3개 이상 4개 이하로 작성하라. 5개 이상 금지, 2개 이하 금지.
- 가장 핵심적이고 치명적인 행동만 선별하라. 사소한 것은 제외.

[규칙 2] behavior 작성법
- `behavior`는 대화 내역 인용이 아니라 행동 자체를 지칭하는 명사구로 작성하라.
  예) "내가 잘못해줄게" → "상대에게 책임 전가"
  예) "지금 어디야?" 반복 → "위치·행방 집착"

[규칙 3] count 산정
- `count`는 해당 행동 패턴이 제공된 대화에서 실제로 등장한 발화 횟수를 직접 세어 기입하라.
- 확인할 수 없으면 0으로 기입하라. 추정·추측 금지.

[규칙 4] likability_score 기준 (0 ~ -100)
- 해당 행동이 관계를 망치는 치명도를 나타낸다. 더 심각할수록 -100에 가깝다.
  자해·자살 협박: -90 ~ -100
  신체 협박·위협: -70 ~ -89
  폭언·모욕: -40 ~ -69
  위치 집착·통제: -30 ~ -59
  과도한 연락 통제: -20 ~ -39
  의심·감시: -10 ~ -29
  해당없음: 0

[규칙 5] danger_type
- 위 분류 체계 중 해당하는 유형명을 그대로 사용하라.
  허용 값: "정서적 폭력" / "통제" / "복합피해 위험" / "해당없음"

[규칙 6] evidence (증거 인용)
- 제공된 대화에 실제로 존재하는 문장만 그대로 인용하라.
- 없는 내용을 지어내거나 변형하는 것은 절대 금지.
- 해당하는 발화가 대화에 없으면 반드시 null로 기입하라.

[규칙 7] analysis_items 정렬
- count 높은 순으로 정렬하라. count가 같으면 likability_score 낮은 순(더 위험한 것 먼저).

[규칙 8] description 길이
- `description`은 50자 이내로 작성하라. 초과 금지.
- `detail`도 50자 이내로 작성하라. 초과 금지.

[규칙 9] compatibility_issues severity 기준
- analysis_items 중 likability_score -60 이하 항목이 포함된 경우: "높음"
- likability_score -30 ~ -59 범위 항목만 있는 경우: "중간"
- likability_score -29 이상 항목만 있는 경우: "낮음"

[규칙 10] danger_level 판정 기준 (아래 조건을 순서대로 확인하고, 처음 해당하는 것으로 결정)
- "위험": analysis_items 중 danger_type이 "복합피해 위험" 이거나 likability_score -70 이하인 항목이 하나라도 있을 때
- "경고": analysis_items 중 danger_type이 "정서적 폭력" 또는 "통제"이고 likability_score -40 이하인 항목이 있을 때
- "주의": 위 두 조건에 해당하지 않지만 부정적 패턴이 존재할 때
- "안전": 부정적 패턴이 전혀 없을 때

[규칙 11] final_verdict.status 허용값
- `status`는 반드시 danger_level과 동일한 값으로 기입하라.
  허용 값: "안전" / "주의" / "경고" / "위험" — 이 외의 표현 사용 금지.

[규칙 12] 모든 분석은 제공된 대화 사실에만 근거하라. 하지도 않은 말·행동 임의 추가 금지.

─────────────────────────────────────────
[출력 형식 - JSON만 반환, 마크다운 코드블록 금지, 설명문 금지]
─────────────────────────────────────────
{{
  "receipt_info": {{ "service_name": "망할연", "target_name": "{target_name}" }},
  "user_type": "{user_type_name}",
  "analysis_items": [
    {{
      "behavior": "행동 명칭 (명사구, 대화 직접 인용 아님)",
      "danger_type": "정서적 폭력 / 통제 / 복합피해 위험 / 해당없음",
      "count": 0,
      "likability_score": 0,
      "description": "요약 (50자 이내)",
      "evidence": "대화에서 실제 존재하는 인용구, 없으면 null"
    }}
  ],
  "compatibility_issues": [
    {{ "issue": "충돌 원인", "severity": "높음/중간/낮음", "detail": "왜 안 맞는지 설명 (300자로 서술) + evidence 포함" }}
  ],
  "danger_level": "안전 / 주의 / 경고 / 위험",
  "danger_comment": "데이트폭력 위험 신호 관련 한줄 코멘트 (위험 신호 없으면 null)",
  "final_verdict": {{ "status": "danger_level과 동일한 값 (안전/주의/경고/위험 중 하나)", "comment": "한줄평" }}
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