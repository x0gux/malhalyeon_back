import os
import json
import pandas as pd
from flask import Flask, render_template, request, jsonify
from langchain_google_genai import ChatGoogleGenerativeAI

# Initialize Flask app
# Since this file is in api/ folder, we need to explicitly set the template and static folder relative to this file
app = Flask(__name__, template_folder='../templates', static_folder='../static')

# 1. 제미나이 모델 설정
# 사용자가 제공한 키 사용 (실제 서비스 배포 시 환경 변수 사용을 권장합니다)
API_KEY = os.environ.get("GOOGLE_API_KEY", "AIzaSyBD1KFLgB1scYcOhuFOt9Um5TwN4khA558")
chat = ChatGoogleGenerativeAI(
    model="models/gemini-2.5-flash",
    google_api_key=API_KEY
)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze_chat():
    try:
        # 1. 파일이 전송되었는지 확인
        if 'file' not in request.files:
            return jsonify({"error": "오류: 'file' 키로 CSV 파일을 업로드해주세요."}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"error": "오류: 선택된 파일이 없습니다."}), 400

        # 2. 카톡 데이터 읽기
        df = pd.read_csv(file)
        chat_log = df.tail(100).to_string() # 최근 100개 대화만 추출

        # 3. 분석 요청 - 기획서 및 영수증 UI 기반 커스텀 프롬프트
        prompt = f"""
역할: 당신은 연애 상담 전문가입니다. 카톡 대화를 분석하여 상대방과 얼마나 잘될지가 아닌 상대방의 유해한 행동 패턴을 분석하여 얼마나 망할지/연애하면 안되는지를 정량화된 JSON 데이터로 반환하는 역할을 수행한다.

분석 대상 대화:
{chat_log}

분석 지침:
1. 행동 추출: 대화에서 발견되는 상대방의 부정적 행동(책임 전가, 가스라이팅, 회피, 단답 등)을 명확한 단어로 정의할 것.
2. 횟수 산정: 해당 행동이 대화 내에서 등장한 횟수를 정수로 카운트할 것.
3. 호감도 산출: 해당 행동이 관계에 미치는 긍정적 영향을 0~100 사이로 산출 (낮을수록 유해하고 타격이 큼).
4. 판정 근거: '연애가 망하는 이유'를 바탕으로 냉정하고 시니컬하게 분석할 것.

출력 형식:
- 반드시 아래 JSON 구조만 반환할 것.
- ```json 이나 ``` 같은 마크다운 코드 블록을 절대 사용하지 말 것.
- 어떠한 사고 과정(thinking)이나 추가 설명 텍스트도 포함하지 말 것.
- 줄바꿈(\\n) 없이 한 줄의 완벽한 JSON string 형태로 반환할 것.

{{ "receipt_info": {{ "service_name": "망할연", "target_name": "상대방" }}, "analysis_items": [ {{ "behavior": "행동 명칭", "count": 0, "likability_score": 0, "description": "시니컬한 한줄 요약" }} ], "final_verdict": {{ "status": "최종 관계 상태 판정", "comment": "냉정한 최종 한줄평" }} }}
"""

        # API 호출
        result = chat.invoke(prompt)
        
        # 반환값이 문자열이므로 JSON으로 파싱하여 반환
        result_content = result.content.strip()
        analysis_data = json.loads(result_content)
        
        return jsonify(analysis_data)

    except json.JSONDecodeError as e:
        # 모델이 JSON 형식을 지키지 않았을 때 예외 처리
        return jsonify({
            "error": "AI 응답을 JSON으로 변환하는 데 실패했습니다.",
            "raw_response": result.content if 'result' in locals() else str(e)
        }), 500
    except pd.errors.EmptyDataError:
        return jsonify({"error": "업로드된 CSV 파일이 비어있습니다."}), 400
    except BaseException as e:
        return jsonify({"error": f"서버 내부 오류: {str(e)}"}), 500

# IMPORTANT: Do not include app.run("0.0.0.0") here. 
# Vercel's serverless environment handles running the app.
