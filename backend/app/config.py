"""
Configuration management
Loads settings uniformly from the .env file at the project root
"""

import os
from dotenv import load_dotenv

# Load .env from project root
# Path: MiroFish/.env (relative to backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # If no root .env, fall back to environment variables (production)
    load_dotenv(override=True)


class Config:
    """Flask configuration class"""
    
    # Flask settings
    # SECRET_KEY must be provided via env in production; dev fallback only in debug mode
    SECRET_KEY = os.environ.get('SECRET_KEY')
    # Safe default: DEBUG off to avoid exposing Werkzeug interactive debugger (RCE risk) in containers
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'

    # CORS allowed origins: same-origin by default (empty); comma-separated list or '*' for all
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '')
    
    # JSON: disable ASCII escaping so CJK renders directly (not \uXXXX)
    JSON_AS_ASCII = False

    # LLM prompt language: which app/prompts/prompt_{lang}.yaml file to load.
    # zh (default) uses legacy prompts.yaml; en/ko etc. use prompt_{lang}.yaml.
    # Set via run.py --prompt-lang flag or PROMPT_LANG env var.
    PROMPT_LANG = os.environ.get('PROMPT_LANG', 'zh')
    
    # LLM settings (OpenAI-compatible format)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    # Graphiti knowledge-graph LLM (extraction, dedup, timestamps, etc.; falls back to LLM_MODEL_NAME)
    GRAPHITI_LLM_MODEL_NAME = os.environ.get('GRAPHITI_LLM_MODEL_NAME', '') or LLM_MODEL_NAME

    # Graphiti LLM client type (how structured output is requested from the model):
    #   - 'generic' (default): graphiti.OpenAIGenericClient, standard /chat/completions,
    #     works with all OpenAI-compatible providers (Alibaba qwen/dashscope, DeepSeek, vLLM, Ollama, etc.).
    #   - 'openai': graphiti.OpenAIClient, OpenAI Responses API + structured parse,
    #     OpenAI official endpoints only; third-party endpoints return truncated/invalid JSON.
    GRAPHITI_LLM_CLIENT = os.environ.get('GRAPHITI_LLM_CLIENT', 'generic').lower()
    # Structured output mode for generic client:
    #   - 'json_schema' (default): native response_format=json_schema; more stable on modern
    #     OpenAI-compatible endpoints (vLLM/llama.cpp/most proxies) and avoids schema echo.
    #   - 'json_object': inject schema into prompt; fallback when provider rejects json_schema.
    LLM_STRUCTURED_OUTPUT_MODE = os.environ.get('LLM_STRUCTURED_OUTPUT_MODE', 'json_schema').lower()

    # ===== Knowledge graph: Graphiti + FalkorDB (self-hosted, replaces Zep Cloud) =====
    # FalkorDB connection (docker-compose overrides host to service name falkordb)
    GRAPH_DB_HOST = os.environ.get('GRAPH_DB_HOST', 'localhost')
    GRAPH_DB_PORT = int(os.environ.get('GRAPH_DB_PORT', '6379'))
    GRAPH_DB_USERNAME = os.environ.get('GRAPH_DB_USERNAME', '') or None
    GRAPH_DB_PASSWORD = os.environ.get('GRAPH_DB_PASSWORD', '') or None
    # Graph database name inside FalkorDB (graph_ids isolated via group_id)
    GRAPH_DB_NAME = os.environ.get('GRAPH_DB_NAME', 'mirofish')

    # Embedding settings (Graphiti requires vectors; defaults reuse LLM credentials)
    EMBEDDER_API_KEY = os.environ.get('EMBEDDER_API_KEY', '') or LLM_API_KEY
    EMBEDDER_BASE_URL = os.environ.get('EMBEDDER_BASE_URL', '') or LLM_BASE_URL
    EMBEDDER_MODEL_NAME = os.environ.get('EMBEDDER_MODEL_NAME', 'text-embedding-3-small')
    # Dimension must match embedding model (OpenAI text-embedding-3-small=1536,
    # Alibaba text-embedding-v3=1024). FalkorDB vector index depends on this.
    EMBEDDER_DIM = int(os.environ.get('EMBEDDER_DIM', '1536'))
    
    # File upload settings
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # Text processing settings
    DEFAULT_CHUNK_SIZE = 500  # default chunk size
    DEFAULT_CHUNK_OVERLAP = 50  # default overlap
    # Graph build: text chunks per Graphiti batch (episodes still processed sequentially)
    DEFAULT_GRAPH_BUILD_BATCH_SIZE = int(os.environ.get('GRAPH_BUILD_BATCH_SIZE', '1'))
    
    # OASIS simulation settings
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS platform action sets
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent settings
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls) -> list[str]:
        """Validate required configuration"""
        errors: list[str] = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY is not configured")
        if not cls.EMBEDDER_API_KEY:
            errors.append("EMBEDDER_API_KEY is not configured (embedding service; defaults to LLM_API_KEY)")
        # Production (non-debug) requires explicit SECRET_KEY
        if not cls.DEBUG and not cls.SECRET_KEY:
            errors.append("SECRET_KEY is not configured (required in production)")
        return errors
