"""
正文翻译运行内去重缓存模块。

该模块只服务于单次 `translate_text()` 工作流，
用于在送入提示词构造前按正文内容去重，减少同轮重复文本的模型请求量。

缓存设计固定为两个容器：
1. `seen_keys`：记录本轮已经放行过的正文键。
2. `duplicate_items`：记录被压掉、等待成功结果回填的重复正文条目。
"""

from __future__ import annotations

import json

from app.models.schemas import TranslationItem


class TranslationCache:
    """
    单轮正文翻译使用的请求级去重缓存。

    设计约束：
    1. 去重键只由 `original_lines`、`item_type`、`role` 组成，不包含地图名等额外上下文。
    2. 首次命中的条目允许继续进入提示词构造。
    3. 后续命中的重复条目会暂存起来，等待首条成功翻译后复用结果。
    """

    def __init__(self) -> None:
        """初始化本轮翻译所需的两个内存容器。"""
        self.seen_keys: set[str] = set()
        self.duplicate_items: dict[str, list[TranslationItem]] = {}

    def build_cache_key(self, item: TranslationItem) -> str:
        """
        为单个正文条目构造稳定去重键。

        Args:
            item: 当前待判断的翻译条目。

        Returns:
            由 `original_lines`、`item_type` 与 `role` 组成的稳定 JSON 字符串。
        """
        cache_payload: dict[str, str | list[str] | None] = {
            "original_lines": list(item.original_lines),
            "item_type": item.item_type,
            "role": item.role,
        }
        return json.dumps(
            cache_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def remember_or_defer(self, item: TranslationItem) -> bool:
        """
        记录首条正文或暂存重复正文。

        Args:
            item: 当前待处理的翻译条目。

        Returns:
            `True` 表示这是首条命中，应继续送入后续提示词构造；
            `False` 表示这是重复条目，已经被暂存。
        """
        cache_key: str = self.build_cache_key(item)
        if cache_key not in self.seen_keys:
            self.seen_keys.add(cache_key)
            return True

        self.duplicate_items.setdefault(cache_key, []).append(item)
        return False

    def pop_duplicate_items(self, item: TranslationItem) -> list[TranslationItem]:
        """
        取出与成功正文同键的全部重复条目。

        Args:
            item: 已经成功翻译的首条正文。

        Returns:
            之前被压掉的重复条目列表；若不存在则返回空列表。
        """
        cache_key: str = self.build_cache_key(item)
        return self.duplicate_items.pop(cache_key, [])


__all__: list[str] = ["TranslationCache"]
