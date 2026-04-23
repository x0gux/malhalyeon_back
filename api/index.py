import os
from flask import Flask
from flasgger import Swagger
from flask_cors import CORS

from api.routes.home import home_bp
from api.routes.quiz import quiz_bp
from api.routes.analyze import analyze_bp
from api.routes.mypage import mypage_bp

app = Flask(__name__, template_folder='../templates', static_folder='../static')
CORS(app, resources={r"/api/*": {"origins": "*"}})

app.config['SWAGGER'] = {
    'title': '야, 너두? 망할연 API',
    'uiversion': 3,
    'description': '카카오톡 대화 분석을 통해 유해한 관계 패턴을 정량화하여 제공하는 API입니다.',
    'specs_route': '/apidocs/'
}

app.register_blueprint(home_bp)
app.register_blueprint(quiz_bp)
app.register_blueprint(analyze_bp)
app.register_blueprint(mypage_bp)

swagger = Swagger(app)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)