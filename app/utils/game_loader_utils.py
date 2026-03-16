"""
游戏数据加载工具模块。

负责从游戏目录读取 `data/` 与 `js/plugins.js`，
并将原始文件内容转换为 `GameData` 纯数据对象。
这是对 `app/models/loaders.py` 的独立复制版本，供新路径直接使用。
"""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import aiofiles
import demjson3
from pydantic import TypeAdapter

from app.models.game_data import BaseItem, CommonEvent, MapData, System, Troop
from app.models.schemas import (
    COMMON_EVENTS_FILE_NAME,
    DATA_DIRECTORY_NAME,
    DATA_ORIGIN_DIRECTORY_NAME,
    FIXED_FILE_NAMES,
    GameData,
    JS_DIRECTORY_NAME,
    MAP_PATTERN,
    PLUGINS_FILE_NAME,
    PLUGINS_ORIGIN_FILE_NAME,
    PLUGINS_JS_PATTERN,
    SYSTEM_FILE_NAME,
    TROOPS_FILE_NAME,
)
from app.utils import run_dialogue_probe


async def load_game_data(game_path: str | Path) -> GameData:
    """
    从指定的 RPG Maker 游戏根目录异步加载、解析所有核心 JSON 文件，并构造出一个完备的 `GameData` 实例。

    整个加载流程利用 `asyncio.gather` 实现了无阻塞的并发文件读取，随后通过 Pydantic 的
    `TypeAdapter` 将松散的 JSON 数据反序列化成带有严格类型的模型树。最后在组装为 `GameData` 之前，
    会挂载一次 `run_dialogue_probe` 探针检查，防止因意外的脚本导致项目崩溃。

    Args:
        game_path: 指向 RPG Maker 游戏根目录的系统路径。

    Returns:
        构造完成且包含所有内存态和可写副本态的 `GameData` 聚合模型。

    Raises:
        FileNotFoundError: 当源数据目录或源 `plugins.js` 不存在时抛出。
        ValueError: 目录处于半成品布局、缺失 `System.json` / `CommonEvents.json` 等核心文件，
            或者对话探针未通过时抛出。
    """
    game_root: Path = Path(game_path)
    source_data_dir, source_plugins_path, _ = resolve_game_source_paths(game_root)

    valid_files: list[Path] = sorted(
        (
            file_path
            for file_path in source_data_dir.iterdir()
            if file_path.is_file() and _is_valid_filename(file_path.name)
        ),
        key=lambda file_path: file_path.name,
    )

    file_contents: list[str] = await asyncio.gather(
        *(_read_text_file(file_path) for file_path in valid_files)
    )

    data: dict[str, Any] = {}
    map_data: dict[str, MapData] = {}
    system: System | None = None
    common_events: list[CommonEvent | None] | None = None
    troops: list[Troop | None] | None = None
    base_data: dict[str, list[BaseItem | None]] = {}
    plugins_js: list[dict[str, Any]] = []

    common_events_adapter: TypeAdapter[list[CommonEvent | None]] = TypeAdapter(
        list[CommonEvent | None]
    )
    troops_adapter: TypeAdapter[list[Troop | None]] = TypeAdapter(list[Troop | None])
    base_data_adapter: TypeAdapter[list[BaseItem | None]] = TypeAdapter(
        list[BaseItem | None]
    )

    for file_path, content in zip(valid_files, file_contents, strict=True):
        file_name: str = file_path.name
        data[file_name] = json.loads(content)

        if MAP_PATTERN.fullmatch(file_name):
            map_data[file_name] = MapData.model_validate_json(content)
            continue

        if file_name == SYSTEM_FILE_NAME:
            system = System.model_validate_json(content)
        elif file_name == COMMON_EVENTS_FILE_NAME:
            common_events = common_events_adapter.validate_json(content)
        elif file_name == TROOPS_FILE_NAME:
            troops = troops_adapter.validate_json(content)
        else:
            base_data[file_name] = base_data_adapter.validate_json(content)

    plugins_content: str = await _read_text_file(source_plugins_path)
    data[PLUGINS_FILE_NAME] = plugins_content
    plugins_js = _parse_plugins_js_text(plugins_content)

    if system is None or common_events is None or troops is None:
        raise ValueError("游戏缺少必要文件，禁止启动")

    run_dialogue_probe(
        map_data=map_data,
        common_events=common_events,
        troops=troops,
    )

    return GameData(
        data=data,
        writable_data=copy.deepcopy(data),
        map_data=map_data,
        system=system,
        common_events=common_events,
        troops=troops,
        base_data=base_data,
        plugins_js=plugins_js,
        writable_plugins_js=copy.deepcopy(plugins_js),
    )


def resolve_game_source_paths(game_root: Path) -> tuple[Path, Path, bool]:
    """
    根据是否存在原件备份，解析本次应读取的源数据路径。

    规则固定为三态：
    1. `data_origin/` 与 `js/plugins_origin.js` 同时存在，视为已翻译布局，工具始终读取原件。
    2. 两者都不存在，视为原始布局，工具直接读取 `data/` 与 `js/plugins.js`。
    3. 只存在其一，视为半成品目录，直接报错禁止继续工作。

    Args:
        game_root: 游戏根目录。

    Returns:
        依次返回：源数据目录、源插件配置路径、是否存在原件备份。

    Raises:
        FileNotFoundError: 需要读取的源目录或源插件文件不存在时抛出。
        ValueError: 当目录处于半成品布局时抛出。
    """
    active_data_dir = game_root / DATA_DIRECTORY_NAME
    active_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_FILE_NAME
    origin_data_dir = game_root / DATA_ORIGIN_DIRECTORY_NAME
    origin_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_ORIGIN_FILE_NAME

    has_origin_data_dir = origin_data_dir.exists()
    has_origin_plugins_path = origin_plugins_path.exists()

    if has_origin_data_dir != has_origin_plugins_path:
        raise ValueError(
            "检测到半成品翻译布局：`data_origin/` 与 `js/plugins_origin.js` 必须同时存在或同时不存在"
        )

    is_translated_layout = has_origin_data_dir and has_origin_plugins_path
    source_data_dir = origin_data_dir if is_translated_layout else active_data_dir
    source_plugins_path = (
        origin_plugins_path if is_translated_layout else active_plugins_path
    )

    if not source_data_dir.exists():
        raise FileNotFoundError(f"数据目录不存在: {source_data_dir}")
    if not source_plugins_path.exists():
        raise FileNotFoundError(f"插件配置文件不存在: {source_plugins_path}")

    return source_data_dir, source_plugins_path, is_translated_layout


async def _read_text_file(file_path: Path) -> str:
    """
    使用 `aiofiles` 以 UTF-8 异步读取文本文件。

    Args:
        file_path: 待读取的文件路径。

    Returns:
        文件的完整文本内容。
    """
    async with aiofiles.open(file_path, "r", encoding="utf-8") as file:
        return await file.read()


def _is_valid_filename(file_name: str) -> bool:
    """
    判断文件名是否为有效的 RPG Maker 数据文件。

    Args:
        file_name: 待检查的文件名。

    Returns:
        若文件名属于已知固定文件或地图文件，则返回 `True`。
    """
    return file_name in FIXED_FILE_NAMES or MAP_PATTERN.fullmatch(file_name) is not None


def _parse_plugins_js_text(plugins_content: str) -> list[dict[str, Any]]:
    """
    利用正则表达式结合 `demjson3`，从包含 JS 变量声明的文本中解析出纯净的插件字典列表。

    RPG Maker 的 `plugins.js` 并不是标准的 JSON 文件，而是一个 JS 脚本文件，
    其主体格式为 `var $plugins = [{...}, {...}];`。
    此方法负责将核心的数组数据剥离出来并完成结构化解析。

    Args:
        plugins_content: 读取自 `plugins.js` 文件的全量字符串文本。

    Returns:
        解析清洗后，由纯粹的字典对象组成的插件列表。若未能成功匹配或解析报错，则安全地返回空列表。
    """
    match = PLUGINS_JS_PATTERN.search(plugins_content)
    if match is None:
        return []

    plugins_array_text: str = match.group(1)

    try:
        decoded: Any = demjson3.decode(plugins_array_text)
    except Exception:
        return []

    if not isinstance(decoded, list):
        return []

    return [plugin for plugin in decoded if isinstance(plugin, dict)]


__all__: list[str] = ["load_game_data", "resolve_game_source_paths"]
