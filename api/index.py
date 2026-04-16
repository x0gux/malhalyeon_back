import os
import json
import re
import time
import pandas as pd
from flask import Flask, render_template, request, jsonify
from langchain_google_genai import ChatGoogleGenerativeAI
from flasgger import Swagger

app = Flask(__name__, template_folder='../templates', static_folder='../static')
swagger = Swagger(app)

# 1. 제미나이 모델 설정
API_KEY = os.environ.get("ai_key")
chat = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=API_KEY,
    transport="rest"
)

# [추가] 서버 과부하(503) 대비 재시도 함수
def invoke_with_retry(chat_model, prompt_text, max_retries=3):
    for i in range(max_retries):
        try:
            return chat_model.invoke(prompt_text)
        except Exception as e:
            if "503" in str(e) or "high demand" in str(e).lower():
                wait_time = (2 ** i)
                print(f"서버 과부하 재시도 중... ({i+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                raise e
    raise Exception("AI 서버 응답 지연으로 분석에 실패했습니다.")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze_chat():
    """
    카카오톡 대화 내용 분석 API
    ---
    tags:
      - Analysis
    parameters:
      - name: target_name
        in: formData
        type: string
        required: true
        description: 분석할 상대방의 이름
      - name: file
        in: formData
        type: file
        required: true
        description: 카톡 대화 CSV 파일
    responses:
      200:
        description: 분석 결과 반환
      400:
        description: 요청 오류
      500:
        description: 서버 오류
    """
    try:
        # 1. 파라미터 체크 (들여쓰기 4칸 유지)
        target_name = request.form.get('target_name')
        if not target_name:
            return jsonify({"error": "분석 대상자 이름을 입력해주세요."}), 400

        if 'file' not in request.files:
            return jsonify({"error": "CSV 파일을 업로드해주세요."}), 400
        
        file = request.files['file']
        
        # 2. 데이터 읽기
        try:
            df = pd.read_csv(file, encoding='utf-8-sig')
        except:
            file.seek(0)
            df = pd.read_csv(file, encoding='cp949')

        chat_log = df.tail(300).to_string()

        # 3. 프롬프트 설정 (멀티라인 스트링은 들여쓰기 영향을 덜 받지만, 가독성을 위해 정리)
        prompt = f"""
역할: 당신은 연애 상담 전문가입니다. 카톡 대화를 분석하여 '{target_name}'님의 유해한 행동 패턴을 분석하여 정량화된 JSON 데이터로 반환하라.

분석 대상 대화:
{chat_log}

출력 형식 (반드시 아래 구조의 JSON만 반환, 마크다운 금지):
{{
  "receipt_info": {{ "service_name": "망할연", "target_name": "{target_name}" }},
  "analysis_items": [
    {{ "behavior": "행동 명칭", "count": 0, "likability_score": 0, "description": "요약", "evidence": "인용구" }}
  ],
  "final_verdict": {{ "status": "판정", "comment": "한줄평" }}
}}
"""

        # 4. AI 호출 및 정제
        result = invoke_with_retry(chat, prompt)
        if isinstance(result.content, list):
            # 리스트 내부의 텍스트 요소들만 합쳐서 하나의 문자열로 만듭니다
            content = "".join([part.get("text", "") if isinstance(part, dict) else str(part) for part in result.content])
        else:
            content = str(result.content)

        content = content.strip()

        # JSON 부분만 추출하는 Regex
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
                
        if json_match:
            json_str = json_match.group().replace('\n', '')
            analysis_data = json.loads(json_str)
            return jsonify(analysis_data)
        else:
            return jsonify({"error": "JSON 형식을 찾을 수 없습니다.", "raw": content}), 500

    except Exception as e:
        return jsonify({"error": f"서버 오류: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)