"""火山 Ark SDK 的最小本地类型存根。"""

class _Completions:
    async def create(self, *, messages: list[dict[str, str]], model: str, stream: bool = False) -> object: ...

class _Chat:
    completions: _Completions

class AsyncArk:
    chat: _Chat

    def __init__(self, *, api_key: str, base_url: str, timeout: int) -> None: ...
