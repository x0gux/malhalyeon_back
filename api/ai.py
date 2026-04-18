import os
import time
import re
from langchain_google_genai import ChatGoogleGenerativeAI

API_KEY = os.environ.get("ai_key")

chat = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash-lite",
    google_api_key=API_KEY,
    transport="rest",
    max_output_tokens=8192
)

# ───────────────────────────────────────────
# Rate limit & Retry Logic
# ───────────────────────────────────────────
_call_times = []
MAX_CALLS_PER_MINUTE = 4

def wait_if_rate_limited():
    now = time.time()
    _call_times[:] = [t for t in _call_times if now - t < 60]
    if len(_call_times) >= MAX_CALLS_PER_MINUTE:
        oldest = _call_times[0]
        wait_sec = 60 - (now - oldest) + 1
        print(f"분당 호출 한도 도달 — {wait_sec:.1f}초 대기")
        time.sleep(wait_sec)
        _call_times[:] = [t for t in _call_times if time.time() - t < 60]
    _call_times.append(time.time())

def invoke_with_retry(prompt_text, max_retries=3):
    '''Uses the global chat instance with retry backoff logic.'''
    for i in range(max_retries):
        try:
            wait_if_rate_limited()
            return chat.invoke(prompt_text)
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait_sec = 60
                match = re.search(r'retry in (\d+)', err)
                if match:
                    wait_sec = int(match.group(1)) + 2
                print(f"Rate limit 초과 — {wait_sec}초 대기 후 재시도 ({i+1}/{max_retries})")
                time.sleep(wait_sec)
                _call_times.clear()
            elif "503" in err or "high demand" in err.lower():
                wait_sec = 2 ** i
                print(f"서버 과부하 재시도 중... ({i+1}/{max_retries})")
                time.sleep(wait_sec)
            else:
                raise e
    raise Exception("AI 서버 응답 지연으로 분석에 실패했습니다.")
