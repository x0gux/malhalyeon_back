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

# 1. 제미나이 모델 설정 (transport="rest" 추가로 gRPC 에러 방지)
API_KEY = os.environ.get("ai_key")
chat = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash", # 2.5 모델 불안정 시 1.5-flash 권장
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
    raise Exception("AI 서버 응답 지연으로 분석에 실패했습니다. 잠시 후 다시 시도해주세요.")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze_chat():
    """
    (중략: Swagger 문서 내용은 동일)
    """
    try:
        target_name = request.form.get('target_name')
        if not target_name:
            return jsonify({"error": "분석 대상자 이름을 입력해주세요."}), 400

        if 'file' not in request.files:
            return jsonify({"error": "CSV 파일을 업로드해주세요."}), 400
        
        file = request.files['file']
        
        # 2. 카톡 데이터 읽기 (인코딩 에러 방지 위해 utf-8-sig 권장)
        try:
            df = pd.read_csv(file, encoding='utf-8-sig')
        except:
            file.seek(0)
            df = pd.read_csv(file, encoding='cp949') # 윈도우용 카톡 대비

        chat_log = df.tail(300).to_string() # 토큰 제한 고려하여 300~500행 권장

        # 3. 분석 프롬프트 (최종본)
        prompt = f"""

역할: 당신은 연애 상담 전문가입니다. 카톡 대화를 분석하여 '{target_name}'님과 얼마나 잘될지가 아닌 '{target_name}'님의 유해한 행동 패턴을 분석하여 얼마나 망할지/연애하면 안되는지를 정량화된 JSON 데이터로 반환하는 역할을 수행한다.



분석 대상 대화:

{chat_log}



분석 지침:

1. 맥락 파악 (Contextual Analysis): '{target_name}'님의 발언이 단순한 '유해성'인지, 아니면 '바쁜 상황(예: 과제, 시험, 업무 등)'에 의한 정당한 반응인지 대화의 전후 문맥을 면밀히 분석할 것.

2. 행동 추출: 문맥상 단순히 바쁜 것이 아니라, 상대를 교묘하게 가지고 놀거나, 책임을 전의하거나, 정서적으로 학대하는(가스라이팅, 회피형 방치, 무시 등) 패턴이 보일 때만 이를 유해 행동으로 정의할 것.

3. 횟수 산정: 해당 행동이 대화 내에서 등장한 횟수를 정수로 카운트할 것.

4. 호감도 산출: 해당 행동이 관계에 미치는 부정적인 영향을 0~100 사이로 산출 (낮을수록 유해하고 타격이 큼). 상황이 참작 가능하면 점수를 높게, 악의적이면 낮게 산출할 것.

5. 판정 근거: '연애가 망하는 이유'를 바탕으로 냉정하고 시니컬하게 분석하되, 실제 상황적 맥락을 고려하여 억까(억지로 까기)를 피할 것.

6. 실제 증거: 해당 행동을 판단하게 된 결정적인 근거가 되는 상대방('{target_name}'님)의 실제 카톡 텍스트 인용구를 추출할 것.



출력 형식:

- 반드시 아래 JSON 구조만 반환할 것.

- ```json 이나 ``` 같은 마크다운 코드 블록을 절대 사용하지 말 것.

- 어떠한 사고 과정(thinking)이나 추가 설명 텍스트도 포함하지 말 것.

- 줄바꿈(\\n) 없이 한 줄의 완벽한 JSON string 형태로 반환할 것.

{{ "receipt_info": {{ "service_name": "망할연", "target_name": "{target_name}" }}, "analysis_items": [ {{ "behavior": "행동 명칭", "count": 0, "likability_score": 0, "description": "시니컬한 요약", "evidence": "실제 발언" }} ], "final_verdict": {{ "status": "최종 판정", "comment": "냉정한 한줄평" }} }}
"""

        # 4. API 호출 (재시도 로직 적용)
        result = invoke_with_retry(chat, prompt)
        
        # 5. 데이터 정제 (Regex 사용)
        # AI 응답에서 { } 로 둘러싸인 JSON 부분만 추출
        content = result.content.strip()
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        
        if json_match:
            json_str = json_match.group().replace('\n', '')
            analysis_data = json.loads(json_str)
            return jsonify(analysis_data)
        else:
            raise ValueError("응답에서 JSON 구조를 찾을 수 없습니다.")

    except json.JSONDecodeError:
        return jsonify({"error": "분석 데이터 형식이 올바르지 않습니다.", "raw": content}), 500
    except Exception as e:
        return jsonify({"error": f"서비 내부 오류: {str(e)}"}), 500

# Vercel 배포 시에는 app.run이 필요 없음