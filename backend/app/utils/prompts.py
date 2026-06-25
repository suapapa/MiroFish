"""
LLM 프롬프트 로더

모든 LLM 프롬프트 텍스트는 ``app/prompts/prompts.yaml`` 에서 관리한다.
코드에서는 ``get_prompt("namespace.key")`` 로 점 표기 키를 사용해 조회한다.

주의:
- ``{var}`` 형태는 호출부에서 ``str.format()`` 으로 치환되는 placeholder 다.
- ``{{`` / ``}}`` 는 ``.format()`` 이후 리터럴 중괄호로 남는다 (JSON 예시 등).
- 언어 지시문 결합 등 런타임 로직은 호출부 코드에 남아 있다.
"""

import functools
import os
from typing import Any, Dict

import yaml

_PROMPTS_PATH = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'prompts.yaml')


@functools.lru_cache(maxsize=1)
def _load_prompts() -> Dict[str, Any]:
    """prompts.yaml 을 한 번만 로드해 캐싱한다."""
    with open(_PROMPTS_PATH, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"프롬프트 파일 형식이 올바르지 않습니다: {_PROMPTS_PATH}")
    return data


def get_prompt(key: str) -> str:
    """
    점 표기 키로 프롬프트 문자열을 조회한다.

    Args:
        key: 예) "report_agent.plan_system"

    Returns:
        프롬프트 문자열

    Raises:
        KeyError: 키가 존재하지 않을 때
        TypeError: 해당 값이 문자열이 아닐 때
    """
    node: Any = _load_prompts()
    for part in key.split('.'):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"프롬프트 키를 찾을 수 없습니다: {key}")
        node = node[part]
    if not isinstance(node, str):
        raise TypeError(f"프롬프트 키가 문자열이 아닙니다: {key}")
    return node
