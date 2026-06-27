"""
LLM prompt loader

All LLM prompt text is managed in ``app/prompts/prompts.yaml``.
Code loads prompts via ``get_prompt("namespace.key")`` dot notation.

Notes:
- ``{var}`` placeholders are substituted by callers via ``str.format()``.
- ``{{`` / ``}}`` remain as literal braces after ``.format()`` (e.g. JSON examples).
- Runtime logic such as language instruction merging stays in calling code.
"""

import functools
import os
from typing import Any, Dict

import yaml

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'prompts')

# Select language-specific prompt file.
#  - PROMPT_LANG env (e.g. en / ko / zh) chooses which yaml to load.
#  - Uses prompt_{lang}.yaml when present, else default Chinese prompts.yaml.
#  - zh uses legacy prompts.yaml as the default.
_DEFAULT_PROMPTS_FILE = 'prompts.yaml'


def _resolve_prompts_path() -> str:
    lang = (os.environ.get('PROMPT_LANG', 'zh') or 'zh').strip().lower()
    candidates = []
    if lang and lang != 'zh':
        candidates.append(os.path.join(_PROMPTS_DIR, f'prompt_{lang}.yaml'))
    candidates.append(os.path.join(_PROMPTS_DIR, _DEFAULT_PROMPTS_FILE))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[-1]


@functools.lru_cache(maxsize=1)
def _load_prompts() -> Dict[str, Any]:
    """Load and cache the selected language prompt yaml once."""
    prompts_path = _resolve_prompts_path()
    with open(prompts_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid prompt file format: {prompts_path}")
    return data


def get_prompt(key: str) -> str:
    """
    Look up a prompt string by dot-notation key.

    Args:
        key: e.g. "report_agent.plan_system"

    Returns:
        Prompt string

    Raises:
        KeyError: when the key does not exist
        TypeError: when the value is not a string
    """
    node: Any = _load_prompts()
    for part in key.split('.'):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"Prompt key not found: {key}")
        node = node[part]
    if not isinstance(node, str):
        raise TypeError(f"Prompt key is not a string: {key}")
    return node
