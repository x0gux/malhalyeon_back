import os
import json
import re
import time
import pandas as pd
from flask import Flask, render_template, request, jsonify
from langchain_google_genai import ChatGoogleGenerativeAI
from flasgger import Swagger

app = Flask(__name__, template_folder='../templates', static_folder='../static')

app.config['SWAGGER'] = {
    'title': '야, 너두? 망할연 API',
    'uiversion': 3,
    'endpoint': 'apispec_1',
    'description': '카카오톡 대화 분석을 통해 유해한 관계 패턴을 정량화하여 제공하는 API입니다.',
    'specs_route': '/apidocs/'
}
swagger = Swagger(app)

API_KEY = os.environ.get("ai_key")
chat = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=API_KEY,
    transport="rest"
)

# 분당 호출 횟수 추적
_call_times = []
MAX_CALLS_PER_MINUTE = 4  # 5회 한도에서 1회 여유분 확보

def wait_if_rate_limited():
    now = time.time()
    # 1분 이내 호출 기록만 유지
    _call_times[:] = [t for t in _call_times if now - t < 60]
    
    if len(_call_times) >= MAX_CALLS_PER_MINUTE:
        oldest = _call_times[0]
        wait_sec = 60 - (now - oldest) + 1
        print(f"분당 호출 한도 도달 — {wait_sec:.1f}초 대기")
        time.sleep(wait_sec)
        _call_times[:] = [t for t in _call_times if time.time() - t < 60]
    
    _call_times.append(time.time())

def invoke_with_retry(chat_model, prompt_text, max_retries=3):
    for i in range(max_retries):
        try:
            wait_if_rate_limited()
            return chat_model.invoke(prompt_text)
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait_sec = 60
                # 에러 메시지에서 retryDelay 파싱
                match = re.search(r'retry in (\d+)', err)
                if match:
                    wait_sec = int(match.group(1)) + 2
                print(f"Rate limit 초과 — {wait_sec}초 대기 후 재시도 ({i+1}/{max_retries})")
                time.sleep(wait_sec)
                _call_times.clear()  # 카운터 리셋
            elif "503" in err or "high demand" in err.lower():
                wait_sec = 2 ** i
                print(f"서버 과부하 재시도 중... ({i+1}/{max_retries})")
                time.sleep(wait_sec)
            else:
                raise e
    raise Exception("AI 서버 응답 지연으로 분석에 실패했습니다.")

# 이하 라우터 동일
@app.route('/')
def home():
    """
    홈 페이지
    ---
    responses:
      200:
        description: 메인 분석 페이지(index.html)를 반환합니다.
    """
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
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
        description: 분석 대상자의 이름
      - name: file
        in: formData
        type: file
        required: true
        description: 카카오톡 대화 내용 CSV 파일 (utf-8-sig 또는 cp949 인코딩 지원)
    responses:
      200:
        description: 분석 성공 결과
        schema:
          type: object
          properties:
            receipt_info:
              type: object
              properties:
                service_name: {type: string}
                target_name: {type: string}
            analysis_items:
              type: array
              items:
                type: object
                properties:
                  behavior: {type: string}
                  count: {type: integer}
                  likability_score: {type: integer}
                  description: {type: string}
                  evidence: {type: string}
            final_verdict:
              type: object
              properties:
                status: {type: string}
                comment: {type: string}
      400:
        description: 파라미터 부족 (target_name 또는 file 누락)
      500:
        description: 서버 오류 또는 분석 실패
    """
    try:
        target_name = request.form.get('target_name')
        if not target_name:
            return jsonify({"error": "분석 대상자 이름을 입력해주세요."}), 400

        if 'file' not in request.files:
            return jsonify({"error": "CSV 파일을 업로드해주세요."}), 400
        
        file = request.files['file']
        
        try:
            df = pd.read_csv(file, encoding='utf-8-sig')
        except:
            file.seek(0)
            df = pd.read_csv(file, encoding='cp949')

        chat_log = df.tail(300).to_string()

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

        result = invoke_with_retry(chat, prompt)
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

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)