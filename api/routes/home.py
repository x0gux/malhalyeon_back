from flask import Blueprint, render_template

home_bp = Blueprint('home_bp', __name__)

@home_bp.route('/')
def home():
    """
    홈 페이지
    ---
    responses:
      200:
        description: 메인 페이지 반환
    """
    return render_template('index.html')


@home_bp.route('/api/health')
def health():
    """
    헬스 체크 (keep-warm 용)
    ---
    tags:
      - System
    responses:
      200:
        description: 서버 생존 확인. Firebase 등 무거운 의존성은 건드리지 않아 즉시 응답한다.
    """
    # 콜드스타트 방지용 핑 엔드포인트. DB/Firebase를 호출하지 않아야
    # 가볍게 프로세스만 깨운 채로 유지할 수 있다. (GitHub Actions가 주기적으로 호출)
    return {"status": "ok"}, 200
