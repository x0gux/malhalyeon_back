import os
from google import genai

# 1.여기에 발급받은 Gemini API 키를 입력하세요
MY_GEMINI_API_KEY = "AIzaSyBMVtJelBfVgVNo28em-M23FcN4fqUQA1Y"

def get_available_google_models():
    if "YourActualKeyHere" in MY_GEMINI_API_KEY:
        print("❌ 에러: MY_GEMINI_API_KEY 변수에 실제 API 키를 입력해 주세요.")
        return

    try:
        client = genai.Client(api_key=MY_GEMINI_API_KEY)
        print("🤖 === 사용 가능한 전체 구글 모델 리스트 ===")
        
        models = client.models.list()
        
        for model in models:
            # 안전하게 가공하기 위해 문자열 처리로 ID만 먼저 추출
            model_id = model.name.split('/')[-1] if '/' in model.name else model.name
            print(f"\n📌 모델 ID: {model_id}")
            print(f"   전체 이름: {model.name}")
            
            # 모델 객체가 가지고 있는 실제 필드 값들을 안전하게 출력
            if hasattr(model, 'display_name'):
                print(f"   이름: {model.display_name}")
            if hasattr(model, 'description') and model.description:
                print(f"   설명: {model.description}")

    except Exception as e:
        print(f"❌ 에러 발생: {e}")

if __name__ == "__main__":
    get_available_google_models()