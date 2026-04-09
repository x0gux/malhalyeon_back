import os
import json
import pandas as pd
from flask import Flask, render_template, request, jsonify
from langchain_google_genai import ChatGoogleGenerativeAI
from flasgger import Swagger

# Initialize Flask app
# Since this file is in api/ folder, we need to explicitly set the template and static folder relative to this file
app = Flask(__name__, template_folder='../templates', static_folder='../static')
swagger = Swagger(app)

# 1. 제미나이 모델 설정
# 사용자가 제공한 키 사용 (실제 서비스 배포 시 환경 변수 사용을 권장합니다)
API_KEY = os.environ.get("ai_key")
chat = ChatGoogleGenerativeAI(
    model="models/gemini-2.5-flash",
    google_api_key=API_KEY
)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/analyze', methods=['POST'])
def analyze_chat():
    """
    카카오톡 대화 내용 분석 (CSV)
    해당 API는 카카오톡 대화 내용(CSV) 데이터를 받아 제미나이를 통해 유해한 행동 패턴을 분석하고 결과를 반환합니다.
    ---
    tags:
      - Analysis
    consumes:
      - multipart/form-data
    parameters:
      - name: file
        in: formData
        type: file
        required: true
        description: 카카오톡 대화 내용 내보내기 파일 (CSV 형식)
      - name: target_name
        in: formData
        type: string
        required: true
        description: 분석할 상대방의 이름 (카톡방에서의 이름)
    responses:
      200:
        description: 분석이 완료된 JSON 객체를 반환합니다.
        schema:
          type: object
          properties:
            receipt_info:
              type: object
              properties:
                service_name:
                  type: string
                target_name:
                  type: string
            analysis_items:
              type: array
              items:
                type: object
                properties:
                  behavior:
                    type: string
                  count:
                    type: integer
                  likability_score:
                    type: integer
                  description:
                    type: string
                  evidence:
                    type: string
            final_verdict:
              type: object
              properties:
                status:
                  type: string
                comment:
                  type: string
      400:
        description: 잘못된 요청 (파일 누락 등)
      500:
        description: 서버 내부 오류
    """
    try:
        # 1. 대상 이름 및 파일 확인
        target_name = request.form.get('target_name')
        if not target_name:
            return jsonify({"error": "오류: 'target_name' 파라미터를 입력해주세요."}), 400

        if 'file' not in request.files:
            return jsonify({"error": "오류: 'file' 키로 CSV 파일을 업로드해주세요."}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({"error": "오류: 선택된 파일이 없습니다."}), 400

        # 2. 카톡 데이터 읽기
        df = pd.read_csv(file)
        chat_log = df.tail(500).to_string() # 전체 대화만 추출

        # 3. 분석 요청 - 기획서 및 영수증 UI 기반 커스텀 프롬프트
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

{{ "receipt_info": {{ "service_name": "망할연", "target_name": "{target_name}" }}, "analysis_items": [ {{ "behavior": "행동 명칭", "count": 0, "likability_score": 0, "description": "맥락이 반영된 시니컬한 한줄 요약", "evidence": "상대방의 실제 카톡 발언 인용구" }} ], "final_verdict": {{ "status": "최종 관계 상태 판정", "comment": "상황 맥락을 고려한 냉정한 최종 한줄평" }} }}
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
