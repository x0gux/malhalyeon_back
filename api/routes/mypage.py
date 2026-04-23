from flask import Blueprint, request, jsonify
import json
import re
from api.ai import invoke_with_retry

mypage_bp = Blueprint('mypage_bp', __name__)

@mypage_bp.route('/api/mypage/pattern', methods=['POST'])
def analyze_pattern():
    """
    사용자 연애 패턴 분석 API
    ---
    tags:
      - MyPage
    parameters:
      - name: history
        in: body
        required: true
        schema:
          type: array
          items:
            type: object
    responses:
      200:
        description: 패턴 분석 결과
      400:
        description: 데이터 부족
    """
    try:
        data = request.json
        history = data.get('history', [])

        if len(history) < 2:
            return jsonify({"error": "분석 데이터가 부족합니다. 최소 2개 이상의 히스토리가 필요합니다."}), 400

        # AI에게 전달할 히스토리 요약
        # 필요한 정보: 각 분석의 target_name, behavior들, 그리고 전체 점수
        history_summary = []
        for entry in history:
            items = entry.get('analysisItems', []) or entry.get('analysis_items', [])
            behaviors = [item.get('behavior') for item in items]
            score = entry.get('totalScore', 0)
            target = entry.get('targetName', '알 수 없음') or entry.get('receipt_info', {}).get('target_name', '알 수 없음')
            history_summary.append({
                "target": target,
                "behaviors": behaviors,
                "score": score
            })

        prompt = f"""
아래는 유저가 분석한 연애 히스토리 데이터입니다. 
지금까지 만난 사람들의 특징과 반복되는 패턴을 전문가적 시각에서 분석하세요.

[히스토리 데이터]
{json.dumps(history_summary, ensure_ascii=False, indent=2)}

[분석 가이드라인]
1. `top_behaviors`: 히스토리 전체에서 가장 자주 등장하거나 반복되는 상대방의 유해한 행동 TOP 3를 선정하세요.
2. `average_score`: 히스토리들의 평균 호감도 점수(likability_score의 합계 평균)를 계산하세요.
3. `pattern_comment`: 유저가 반복적으로 어떤 유형의 사람에게 끌리는지, 혹은 어떤 연애 환경에 처해 있는지 날카롭고 공감 가는 '한줄 진단'을 작성하세요.

출력 형식 (JSON만 반환):
{{
  "top_behaviors": ["행동1", "행동2", "행동3"],
  "average_score": 0,
  "pattern_comment": "한줄 진단 예시: 잠수 타는 사람에게 반복적으로 끌리는 경향이 있습니다."
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
            return jsonify(json.loads(json_match.group(), strict=False))
        else:
            return jsonify({"error": "패턴 분석 결과 생성에 실패했습니다."}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
