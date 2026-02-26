from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request

try:
    from src.llm import local_secrets as _local_secrets  # type: ignore
except Exception:
    _local_secrets = None


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        local_key = getattr(_local_secrets, 'DEEPSEEK_API_KEY', '') if _local_secrets else ''
        local_model = getattr(_local_secrets, 'DEEPSEEK_MODEL', '') if _local_secrets else ''
        local_base = getattr(_local_secrets, 'DEEPSEEK_BASE_URL', '') if _local_secrets else ''

        self.api_key = (api_key or local_key or os.getenv('DEEPSEEK_API_KEY', '')).strip()
        self.model = (model or local_model or os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')).strip()
        root = (base_url or local_base or os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')).rstrip('/')
        self.url = f'{root}/chat/completions'
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.1) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError('DEEPSEEK_API_KEY is not configured')

        payload = {
            'model': self.model,
            'temperature': temperature,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }
        body = json.dumps(payload).encode('utf-8')
        req = request.Request(
            self.url,
            method='POST',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}',
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = json.loads(resp.read().decode('utf-8'))
        except error.HTTPError as e:
            msg = e.read().decode('utf-8', errors='ignore')
            raise RuntimeError(f'DeepSeek HTTP {e.code}: {msg}') from e
        except error.URLError as e:
            raise RuntimeError(f'DeepSeek network error: {e}') from e

        choices = raw.get('choices', [])
        if not choices:
            raise RuntimeError('DeepSeek response missing choices')
        content = choices[0].get('message', {}).get('content', '').strip()
        if not content:
            raise RuntimeError('DeepSeek response content is empty')
        return json.loads(content)
