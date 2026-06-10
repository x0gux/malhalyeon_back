import os
import time
import re
import threading
from langchain_google_genai import (
    ChatGoogleGenerativeAI,
    HarmCategory,
    HarmBlockThreshold,
)

API_KEY = os.environ.get("ai_key")

# 데이트폭력 시뮬레이션은 집착·통제·위협 언어를 다루므로 안전필터가 응답을
# 통째로 차단(빈 응답)하는 일이 잦다. 차단을 끄지 않으면 상대방 메시지가 비어
# 프론트에 아무것도 표시되지 않는다.
_NO_BLOCK_SAFETY = {
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}

# 분석용 모델: gemini-2.0-flash (무료 티어 200 req/day)
#
# max_retries=0: langchain 기본값(6)은 429/503을 받으면 우리 코드에 예외를 넘기기
# 전에 내부적으로 지수 백오프 재시도를 6번 돌려, 할당량 소진된 모델 한 번에 ~36초를
# 낭비한다. 재시도·모델 폴백은 invoke_with_retry/invoke_analyze가 직접 처리하므로
# 내부 재시도는 끄고 즉시 예외를 받아 우리 로직이 다음 모델로 넘어가게 한다.
chat = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    google_api_key=API_KEY,
    max_output_tokens=4096,
    max_retries=0,
)

# 분석용 모델 폴백 체인: 제한(429)에 걸리면 다음 단계로 승급하며 재시도.
# 1순위를 2.5-flash로 둔다 — 2.0-flash는 무료 티어 200 RPD가 상시 소진돼 1순위면
# 매 분석이 429로 시작했다. 2.0-flash는 2순위 대안으로 남겨 할당량이 리셋된
# 시간대나 2.5-flash가 소진됐을 때 호출되게 한다.
ANALYZE_MODELS = os.environ.get(
    "analyze_models",
    "gemini-2.5-flash,gemini-2.0-flash,gemini-2.5-flash-lite,gemini-3.5-flash",
).split(",")
ANALYZE_MODELS = [m.strip() for m in ANALYZE_MODELS if m.strip()]


def _build_analyze_chat(model: str) -> ChatGoogleGenerativeAI:
    kwargs = dict(
        model=model,
        google_api_key=API_KEY,
        max_output_tokens=8192,
        safety_settings=_NO_BLOCK_SAFETY,
        max_retries=0,  # 내부 재시도 금지 — invoke_analyze가 모델 폴백을 직접 처리
    )
    # gemini-2.5+ flash는 thinking 모델 — thinking 토큰이 output 예산을 먼저
    # 소진하면 JSON 응답이 중간에 잘린다. 분석은 thinking이 불필요하므로 비활성화.
    if "2.0" not in model and "1.5" not in model:
        kwargs["thinking_budget"] = 0
    return ChatGoogleGenerativeAI(**kwargs)

# 시뮬레이션용 모델: gemma-4-31b-it (분석 모델과 별도 할당량)
SIMULATION_MODEL = os.environ.get("simulation_model", "gemma-4-31b-it")
chat_simulation = ChatGoogleGenerativeAI(
    model=SIMULATION_MODEL,
    google_api_key=API_KEY,
    max_output_tokens=1024,  # 시뮬레이션은 짧은 응답만 필요
    safety_settings=_NO_BLOCK_SAFETY,
    max_retries=0,  # gemma 실패 시 즉시 예외 → _opponent_message가 gemini로 폴백
)

# 보조 출력용 모델: 선택지·피드백은 JSON 구조화 출력이라 말투 모방이 불필요해
# 품질 민감도가 낮다. 시뮬레이션(상대방) 모델 gemma-4-31b는 호출당 ~20초로 느려,
# reply 한 번에 상대방 응답 + 선택지 + 피드백을 모두 같은 모델로 호출하면 Vercel
# 함수 타임아웃(→504, CORS 헤더 누락)을 넘긴다. 선택지/피드백만 빠른 모델로 분리해
# 전체 응답 시간을 줄인다. (상대방 응답은 gemma 그대로)
#
# 기본값을 gemini-3.1-flash-lite로 둔다 — ANALYZE_MODELS 체인
# (2.0-flash → 2.5-flash → 2.5-flash-lite → 3.5-flash)과 안 겹쳐 할당량 충돌이
# 없고, 저지연·저비용(thinking 기본값 minimal)이라 구조화 출력·폴백에 적합하다.
# gemini-2.0-flash는 분석 1순위라 소진 시 aux가 매번 429를 맞으므로 피한다.
# (preview만 잡히는 환경이면 env로 gemini-3.1-flash-lite-preview 지정)
AUX_MODEL = os.environ.get("feedback_model", "gemini-3.1-flash-lite")


def _build_aux_chat(model: str) -> ChatGoogleGenerativeAI:
    kwargs = dict(
        model=model,
        google_api_key=API_KEY,
        max_output_tokens=1024,
        safety_settings=_NO_BLOCK_SAFETY,
        max_retries=0,  # 내부 재시도 금지 — invoke_with_retry가 직접 처리(max_backoff 캡)
    )
    # gemini-2.5+/3.x flash는 thinking 모델 — thinking 토큰이 지연·잘림을 유발하므로
    # 구조화 출력엔 비활성화. thinking_budget=0은 3.x에서도 backward-compat로 허용
    # (thinking_level과 동시 지정만 400). 3.1-flash-lite는 기본 minimal이라 더 안전.
    # (gemma·2.0·1.5는 thinking 미지원)
    if "2.0" not in model and "1.5" not in model and "gemma" not in model:
        kwargs["thinking_budget"] = 0
    return ChatGoogleGenerativeAI(**kwargs)


chat_aux = _build_aux_chat(AUX_MODEL)

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
_aux_limiter = RateLimiter(max_per_minute=50)

# 분석 폴백 체인용 모델별 인스턴스 + rate limiter
_analyze_chats = {m: _build_analyze_chat(m) for m in ANALYZE_MODELS}
_analyze_limiters = {m: RateLimiter(max_per_minute=50) for m in ANALYZE_MODELS}

# ───────────────────────────────────────────
# Retry Logic
# ───────────────────────────────────────────
def invoke_with_retry(prompt_text, max_retries=3, chat_instance=None, limiter=None, max_backoff=None):
    """Uses the specified or global chat instance with retry backoff logic.

    max_backoff: 429/503 재시도 대기 시간 상한(초). 서버리스(Vercel) 함수는
    실행시간 제한이 있어 60초씩 대기하면 타임아웃→504로 죽고 CORS 헤더가 빠진다.
    aux처럼 시간이 빠듯한 경로는 작은 값을 줘 빠르게 실패(→Flask가 응답 반환,
    CORS 헤더 유지)하도록 한다. None이면 기존 동작(상한 없음)."""
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
                if max_backoff is not None:
                    wait_sec = min(wait_sec, max_backoff)
                print(f"Rate limit 초과 — {wait_sec}초 대기 후 재시도 ({i+1}/{max_retries})")
                time.sleep(wait_sec)
            elif "503" in err or "high demand" in err.lower():
                wait_sec = 2 ** i
                if max_backoff is not None:
                    wait_sec = min(wait_sec, max_backoff)
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


def invoke_simulation(prompt_text, max_retries=2):
    """상대방 응답(gemma) 전용 호출.

    gemma도 서버리스 경로에서 동작하므로 429/503 backoff를 캡한다 — 캡이 없으면
    429 한 번에 60초씩 sleep해 Vercel 함수 타임아웃(→504, CORS 헤더 누락)을 넘기거나
    재시도 소진 후 generic 예외가 그대로 사용자에게 노출된다. 이 호출이 실패하면
    호출측(_opponent_message)이 빠른 gemini 모델로 폴백한다."""
    return invoke_with_retry(
        prompt_text,
        max_retries,
        chat_instance=chat_simulation,
        limiter=_simulation_limiter,
        max_backoff=4,
    )


def invoke_simulation_fallback(prompt_text, max_retries=2):
    """상대방 응답 폴백 — gemma가 실패(429/503 소진·빈응답)했을 때만 사용.

    말투 모방 품질은 gemma보다 떨어지지만, 빠른 gemini 보조 모델로 자연스러운
    한 줄을 만들어 고정 문구보다 낫고 시뮬레이션 전체가 죽는 것을 막는다.
    서버리스 타임아웃 방지를 위해 backoff를 5초로 캡한다."""
    return invoke_with_retry(
        prompt_text,
        max_retries,
        chat_instance=chat_aux,
        limiter=_aux_limiter,
        max_backoff=5,
    )


def invoke_auxiliary(prompt_text, max_retries=2):
    """선택지·피드백 등 구조화 출력 전용 호출. 빠른 보조 모델을 사용한다.

    서버리스 타임아웃을 넘기지 않도록 재시도 대기를 5초로 캡한다 — 429가 나도
    60초 대기하지 않고 빠르게 실패해, 함수가 죽는 대신 Flask가 (CORS 헤더 붙은)
    오류 응답을 반환한다."""
    return invoke_with_retry(
        prompt_text,
        max_retries,
        chat_instance=chat_aux,
        limiter=_aux_limiter,
        max_backoff=5,
    )
