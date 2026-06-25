"""
MiroFish Backend 启动入口
"""

import argparse
import os
import sys

# 解决 Windows 控制台中文乱码问题：在所有导入之前设置 UTF-8 编码
if sys.platform == 'win32':
    # 设置环境变量确保 Python 使用 UTF-8
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    # 重新配置标准输出流为 UTF-8
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config

# 模块级 WSGI 应用对象，供 gunicorn 等 WSGI 服务器使用（gunicorn run:app）
# 生产环境通过 gunicorn 启动，不会执行下方的开发用 app.run()
app = create_app()


def _parse_args():
    parser = argparse.ArgumentParser(description="MiroFish Backend")
    parser.add_argument(
        '--prompt-lang',
        dest='prompt_lang',
        default=os.environ.get('PROMPT_LANG', 'zh'),
        help="로드할 LLM 프롬프트 언어 (app/prompts/prompt_{lang}.yaml). "
             "예: zh(기본), en, ko",
    )
    # gunicorn 등 외부 실행기가 넘기는 인자와 충돌하지 않도록 알 수 없는 인자는 무시
    args, _ = parser.parse_known_args()
    return args


def main():
    """主函数"""
    # 프롬프트 언어 플래그 적용 (프롬프트는 첫 요청 시 lazy 로드되므로 여기서 설정하면 반영된다)
    args = _parse_args()
    os.environ['PROMPT_LANG'] = args.prompt_lang

    # 验证配置
    errors = Config.validate()
    if errors:
        print("配置错误:")
        for err in errors:
            print(f"  - {err}")
        print("\n请检查 .env 文件中的配置")
        sys.exit(1)
    
    # 复用模块级应用对象（与 gunicorn 使用同一个 create_app 实例）
    # 获取运行配置
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG

    print(f"프롬프트 언어(PROMPT_LANG): {os.environ.get('PROMPT_LANG', 'zh')}")

    # 启动服务
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()

