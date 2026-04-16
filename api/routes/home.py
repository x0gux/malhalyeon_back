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
