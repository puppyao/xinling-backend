from __future__ import annotations

from flask import Flask

from config import config
from database import init_db
from music_routes import init_music_library, music_bp, playlist_sync_service
from storage_routes import storage_bp
from utils import make_response
from chat_routes import chat_bp
from playlist_config_routes import playlist_config_bp
from request_logging import setup_request_logging
from auth_routes import auth_bp



def create_app() -> Flask:
    app = Flask(__name__)
    setup_request_logging(app)

    # 启动初始化：创建表 & 加载音乐库
    init_db()
    init_music_library()
    playlist_sync_service.start_background()

    app.register_blueprint(music_bp)
    app.register_blueprint(storage_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(playlist_config_bp)
    app.register_blueprint(auth_bp)
    
    @app.errorhandler(404)
    def _not_found(_err):
        return make_response(code=1002, message="资源不存在", data=None, http_status=404)

    @app.errorhandler(500)
    def _server_error(_err):
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)

    return app


if __name__ == "__main__":
    create_app().run(host=config.host, port=config.port)

