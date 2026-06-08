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

# 분석용 모델 폴백 체인: 제한(429)에 걸리면 위 단계로 승급하며 재시도
ANALYZE_MODELS = os.environ.get(
    "analyze_models", "gemini-2.0-flash,gemini-2.5-flash,gemini-3.0-flash"
).split(",")
ANALYZE_MODELS = [m.strip() for m in ANALYZE_MODELS if m.strip()]


def _build_analyze_chat(model: str) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=API_KEY,
        transport="rest",
        max_output_tokens=4096,
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

# 분석 폴백 체인용 모델별 인스턴스 + rate limiter
_analyze_chats = {m: _build_analyze_chat(m) for m in ANALYZE_MODELS}
_analyze_limiters = {m: RateLimiter(max_per_minute=50) for m in ANALYZE_MODELS}

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

def invoke_analyze(prompt_text, max_retries=3):
    """분석 전용 호출. 모델 폴백 체인을 따라 호출한다.

    현재 모델이 rate limit(429/RESOURCE_EXHAUSTED)에 걸리면 대기하지 않고
    즉시 다음 모델(gemini-2.0-flash → 2.5-flash → 3.0-flash)로 승급해 재시도한다.
    503(서버 과부하)은 같은 모델에서 backoff 후 재시도한다.
    """
    last_err = None
    for model in ANALYZE_MODELS:
        target_chat = _analyze_chats[model]
        target_limiter = _analyze_limiters[model]
        for i in range(max_retries):
            try:
                target_limiter.wait_if_needed()
                return target_chat.invoke(prompt_text)
            except Exception as e:
                err = str(e)
                last_err = e
                if "429" in err or "RESOURCE_EXHAUSTED" in err:
                    print(f"{model} 제한 도달 — 다음 모델로 승급")
                    break  # 대기 없이 다음 모델로
                elif "503" in err or "high demand" in err.lower():
                    wait_sec = 2 ** i
                    print(f"{model} 서버 과부하 재시도 중... ({i+1}/{max_retries})")
                    time.sleep(wait_sec)
                else:
                    raise e
    raise last_err or Exception("AI 서버 응답 지연으로 분석에 실패했습니다.")


def invoke_simulation(prompt_text, max_retries=3):
    """Uses the simulation model with its own rate limiter."""
    return invoke_with_retry(
        prompt_text,
        max_retries,
        chat_instance=chat_simulation,
        limiter=_simulation_limiter
    )