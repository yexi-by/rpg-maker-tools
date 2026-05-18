"""数据库会话 mixin 的最小状态契约。"""

from pathlib import Path
from typing import cast

import aiosqlite


class SessionMixinBase:
    """声明数据库会话子能力需要的公共状态。"""

    # mixin 只声明会话状态契约；真实连接由 TargetGameSession.__init__ 在入口处写入。
    connection: aiosqlite.Connection = cast(aiosqlite.Connection, object())

    @property
    def db_path(self) -> Path:
        """返回当前会话绑定的数据库路径。"""
        raise NotImplementedError
