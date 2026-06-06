import os
import time
import re
import threading
from langchain_google_genai import ChatGoogleGenerativeAI

API_KEY = os.environ.get("ai_key")

# 분석용 모델: gemini-2.0-flash (무료 티어 200 req/day)
chat = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    google_api_key=API_KEY,
    transport="rest",
    max_output_tokens=4096
)

# 시뮬레이션용 모델: gemma-4-31b-it (분석 모델과 별도 할당량)
SIMULATION_MODEL = os.environ.get("simulation_model", "gemma-4-31b-it")
chat_simulation = ChatGoogleGenerativeAI(
    model=SIMULATION_MODEL,
    google_api_key=API_KEY,
    transport="rest",
    max_output_tokens=1024  # 시뮬레이션은 짧은 응답만 필요
)

# ───────────────────────────────────────────
# 모델별 Rate Limiter (thread-safe)
# ───────────────────────────────────────────
class RateLimiter:
    """Thread-safe per-model rate limiter."""
    def __init__(self, max_per_minute: int = 50):
        self.max_per_minute = max_per_minute
        self._call_times: list[float] = []
        self._lock = threading.Lock()

    def wait_if_needed(self):
        with self._lock:
            now = time.time()
            self._call_times = [t for t in self._call_times if now - t < 60]
            if len(self._call_times) >= self.max_per_minute:
                oldest = self._call_times[0]
                wait_sec = 60 - (now - oldest) + 1
                print(f"분당 호출 한도 도달 — {wait_sec:.1f}초 대기")
            else:
                wait_sec = 0
            self._call_times.append(time.time())

        if wait_sec > 0:
            time.sleep(wait_sec)

_chat_limiter = RateLimiter(max_per_minute=50)
_simulation_limiter = RateLimiter(max_per_minute=50)

# ───────────────────────────────────────────
# Retry Logic
# ───────────────────────────────────────────
def invoke_with_retry(prompt_text, max_retries=3, chat_instance=None, limiter=None):
    """Uses the specified or global chat instance with retry backoff logic."""
    target_chat = chat_instance or chat
    target_limiter = limiter or _chat_limiter
    for i in range(max_retries):
        try:
            target_limiter.wait_if_needed()
            return target_chat.invoke(prompt_text)
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait_sec = 60
                match = re.search(r'retry in (\d+)', err)
                if match:
                    wait_sec = int(match.group(1)) + 2
                print(f"Rate limit 초과 — {wait_sec}초 대기 후 재시도 ({i+1}/{max_retries})")
                time.sleep(wait_sec)
            elif "503" in err or "high demand" in err.lower():
                wait_sec = 2 ** i
                print(f"서버 과부하 재시도 중... ({i+1}/{max_retries})")
                time.sleep(wait_sec)
            else:
                raise e
    raise Exception("AI 서버 응답 지연으로 분석에 실패했습니다.")

def invoke_simulation(prompt_text, max_retries=3):
    """Uses the simulation model with its own rate limiter."""
    return invoke_with_retry(
        prompt_text,
        max_retries,
        chat_instance=chat_simulation,
        limiter=_simulation_limiter
    )