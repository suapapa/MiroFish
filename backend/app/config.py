"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=True)


class Config:
    """Flask配置类"""
    
    # Flask配置
    # 生产环境必须通过环境变量提供 SECRET_KEY；仅在调试模式下回退到开发用默认值
    SECRET_KEY = os.environ.get('SECRET_KEY')
    # 安全默认：DEBUG 默认关闭，避免在容器中暴露 Werkzeug 交互式调试器（RCE 风险）
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'

    # CORS 允许的来源：默认仅同源（空），可用逗号分隔的列表覆盖，'*' 表示全部
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS', '')
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    # Graphiti 抽取阶段使用的小模型（可选，默认与主模型一致）
    LLM_SMALL_MODEL_NAME = os.environ.get('LLM_SMALL_MODEL_NAME', '') or LLM_MODEL_NAME

    # ===== 知识图谱：Graphiti + FalkorDB（自托管，替代 Zep Cloud）=====
    # FalkorDB 连接配置（docker-compose 中通过环境变量覆盖为服务名 falkordb）
    GRAPH_DB_HOST = os.environ.get('GRAPH_DB_HOST', 'localhost')
    GRAPH_DB_PORT = int(os.environ.get('GRAPH_DB_PORT', '6379'))
    GRAPH_DB_USERNAME = os.environ.get('GRAPH_DB_USERNAME', '') or None
    GRAPH_DB_PASSWORD = os.environ.get('GRAPH_DB_PASSWORD', '') or None
    # FalkorDB 内的图数据库名称（各 graph_id 通过 group_id 隔离）
    GRAPH_DB_NAME = os.environ.get('GRAPH_DB_NAME', 'mirofish')

    # Embedding 配置（Graphiti 需要向量嵌入；默认复用 LLM 凭据）
    EMBEDDER_API_KEY = os.environ.get('EMBEDDER_API_KEY', '') or LLM_API_KEY
    EMBEDDER_BASE_URL = os.environ.get('EMBEDDER_BASE_URL', '') or LLM_BASE_URL
    EMBEDDER_MODEL_NAME = os.environ.get('EMBEDDER_MODEL_NAME', 'text-embedding-3-small')
    # 向量维度需与 embedding 模型匹配（OpenAI text-embedding-3-small=1536，
    # 阿里云 text-embedding-v3=1024）。FalkorDB 向量索引依赖此值。
    EMBEDDER_DIM = int(os.environ.get('EMBEDDER_DIM', '1536'))
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置"""
        errors: list[str] = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        if not cls.EMBEDDER_API_KEY:
            errors.append("EMBEDDER_API_KEY 未配置（嵌入服务，默认复用 LLM_API_KEY）")
        # 非调试（生产）模式必须显式配置 SECRET_KEY
        if not cls.DEBUG and not cls.SECRET_KEY:
            errors.append("SECRET_KEY 未配置（生产环境必填）")
        return errors

