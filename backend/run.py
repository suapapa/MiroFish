"""
MiroFish Backend entry point
"""

import argparse
import os
import sys

# Fix Windows console CJK garbling: set UTF-8 before any imports
if sys.platform == 'win32':
    # Ensure Python uses UTF-8
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    # Reconfigure stdout/stderr to UTF-8
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.config import Config

# Module-level WSGI app for gunicorn etc. (gunicorn run:app)
# Production starts via gunicorn; dev app.run() below is not executed
app = create_app()


def _parse_args():
    parser = argparse.ArgumentParser(description="MiroFish Backend")
    parser.add_argument(
        '--prompt-lang',
        dest='prompt_lang',
        default=os.environ.get('PROMPT_LANG', 'zh'),
        help="LLM prompt language to load (app/prompts/prompt_{lang}.yaml). "
             "Examples: zh (default), en, ko",
    )
    # Ignore unknown args so gunicorn and other runners do not conflict
    args, _ = parser.parse_known_args()
    return args


def main():
    """Main entry point"""
    # Apply prompt language flag (prompts lazy-load on first request, so set here)
    args = _parse_args()
    os.environ['PROMPT_LANG'] = args.prompt_lang

    # Validate configuration
    errors = Config.validate()
    if errors:
        print("Configuration error:")
        for err in errors:
            print(f"  - {err}")
        print("\nPlease check the configuration in the .env file")
        sys.exit(1)
    
    # Reuse module-level app (same create_app instance as gunicorn)
    # Runtime settings
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5001))
    debug = Config.DEBUG

    print(f"Prompt language (PROMPT_LANG): {os.environ.get('PROMPT_LANG', 'zh')}")

    # Start server
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == '__main__':
    main()
