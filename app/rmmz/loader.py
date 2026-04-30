"""
游戏加载统一入口模块。

本模块加载 RPG Maker MZ 标准数据文件与 `js/plugins.js`。未知 `data/*.json`
会被跳过并记录 DEBUG 日志。
"""

import asyncio
import copy
import json
from pathlib import Path
from typing import cast

import aiofiles
import demjson3
from pydantic import TypeAdapter

from app.rmmz.game_data import BaseItem, CommonEvent, MapData, System, Troop
from app.rmmz.schema import (
    COMMON_EVENTS_FILE_NAME,
    DATA_DIRECTORY_NAME,
    DATA_ORIGIN_DIRECTORY_NAME,
    FIXED_FILE_NAMES,
    GameData,
    JS_DIRECTORY_NAME,
    MAP_PATTERN,
    PLUGINS_FILE_NAME,
    PLUGINS_JS_PATTERN,
    PLUGINS_ORIGIN_FILE_NAME,
    SYSTEM_FILE_NAME,
    TROOPS_FILE_NAME,
)
from app.rmmz.text_rules import JsonValue, coerce_json_value, ensure_json_object
from app.rmmz.probe import run_dialogue_probe
from app.observability.logging import logger

PACKAGE_FILE_NAME = "package.json"


async def load_game_data(game_path: str | Path) -> GameData:
    """从 RPG Maker 游戏根目录加载标准数据文件并构造 `GameData`。"""
    game_root = Path(game_path)
    source_data_dir, source_plugins_path, _ = resolve_game_source_paths(game_root)
    origin_data_dir = game_root / DATA_ORIGIN_DIRECTORY_NAME

    valid_files = sorted(
        (
            file_path
            for file_path in source_data_dir.iterdir()
            if file_path.is_file() and _is_standard_rmmz_filename(file_path.name)
        ),
        key=lambda file_path: file_path.name,
    )
    _log_skipped_data_files(source_data_dir=source_data_dir, valid_files=valid_files)

    file_contents = await asyncio.gather(
        *(
            _read_text_file(resolve_data_source_file(active_file_path=file_path, origin_data_dir=origin_data_dir))
            for file_path in valid_files
        )
    )

    data: dict[str, JsonValue] = {}
    map_data: dict[str, MapData] = {}
    system: System | None = None
    common_events: list[CommonEvent | None] | None = None
    troops: list[Troop | None] | None = None
    base_data: dict[str, list[BaseItem | None]] = {}

    common_events_adapter: TypeAdapter[list[CommonEvent | None]] = TypeAdapter(
        list[CommonEvent | None]
    )
    troops_adapter: TypeAdapter[list[Troop | None]] = TypeAdapter(list[Troop | None])
    base_data_adapter: TypeAdapter[list[BaseItem | None]] = TypeAdapter(list[BaseItem | None])

    for file_path, content in zip(valid_files, file_contents, strict=True):
        file_name = file_path.name
        json_value = _decode_json_value(content=content, source=file_path)
        data[file_name] = json_value

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

    plugins_content = await _read_text_file(source_plugins_path)
    data[PLUGINS_FILE_NAME] = plugins_content
    plugins_js = _parse_plugins_js_text(plugins_content)

    if system is None or common_events is None or troops is None:
        raise ValueError("游戏缺少 System.json、CommonEvents.json 或 Troops.json，禁止启动")

    run_dialogue_probe(map_data=map_data, common_events=common_events, troops=troops)

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


def resolve_game_directory(game_path: str | Path) -> Path:
    """解析并校验游戏根目录路径。"""
    resolved_path = Path(game_path).resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"游戏目录不存在: {resolved_path}")
    if not resolved_path.is_dir():
        raise NotADirectoryError(f"游戏路径不是目录: {resolved_path}")
    return resolved_path


def read_game_title(game_path: Path) -> str:
    """从游戏目录下的 `package.json` 读取游戏标题。"""
    package_path = game_path / PACKAGE_FILE_NAME
    if not package_path.exists():
        raise FileNotFoundError(f"未找到 package.json: {package_path}")

    raw_text = package_path.read_text(encoding="utf-8")
    package_data = _decode_json_value(content=raw_text, source=package_path)
    package_object = ensure_json_object(package_data, f"{package_path} 顶层")

    window_config = package_object.get("window")
    if not isinstance(window_config, dict):
        raise ValueError(f"package.json 缺少 window 对象: {package_path}")

    title = window_config.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"package.json 缺少有效的 window.title: {package_path}")

    return title.strip()


def resolve_game_source_paths(game_root: Path) -> tuple[Path, Path, bool]:
    """根据是否存在原件备份解析本次应读取的源数据路径。"""
    active_data_dir = game_root / DATA_DIRECTORY_NAME
    active_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_FILE_NAME
    origin_data_dir = game_root / DATA_ORIGIN_DIRECTORY_NAME
    origin_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_ORIGIN_FILE_NAME

    has_origin_data_dir = origin_data_dir.exists()
    has_origin_plugins_path = origin_plugins_path.exists()
    is_translated_layout = has_origin_data_dir or has_origin_plugins_path
    source_plugins_path = origin_plugins_path if has_origin_plugins_path else active_plugins_path

    if not active_data_dir.exists():
        raise FileNotFoundError(f"数据目录不存在: {active_data_dir}")
    if has_origin_data_dir and not origin_data_dir.is_dir():
        raise NotADirectoryError(f"原件数据留档不是目录: {origin_data_dir}")
    if not source_plugins_path.exists():
        raise FileNotFoundError(f"插件配置文件不存在: {source_plugins_path}")

    return active_data_dir, source_plugins_path, is_translated_layout


def resolve_data_source_file(*, active_file_path: Path, origin_data_dir: Path) -> Path:
    """解析单个 data 文件的读取来源，原件留档存在时优先读取留档。"""
    origin_file_path = origin_data_dir / active_file_path.name
    if origin_file_path.exists():
        return origin_file_path
    return active_file_path


class GameDataManager:
    """全局游戏数据管理器。"""

    def __init__(self) -> None:
        """初始化空的游戏数据缓存。"""
        self.items: dict[str, GameData] = {}

    async def load_game_data(self, game_path: str | Path) -> None:
        """读取指定游戏目录，并以游戏标题为键写入缓存。"""
        resolved_game_path = resolve_game_directory(game_path)
        game_title = read_game_title(resolved_game_path)
        source_data_dir, source_plugins_path, has_origin_backup = resolve_game_source_paths(
            resolved_game_path
        )
        game_data = await load_game_data(resolved_game_path)

        if has_origin_backup:
            logger.warning(f"[tag.warning]检测到该游戏已经执行过激活版回写，后续会优先读取受影响文件的原件留档[/tag.warning] 游戏 [tag.count]{game_title}[/tag.count] 数据目录 [tag.path]{source_data_dir}[/tag.path] 插件来源 [tag.path]{source_plugins_path}[/tag.path]")

        self.items[game_title] = game_data


async def _read_text_file(file_path: Path) -> str:
    """使用 UTF-8 异步读取文本文件。"""
    async with aiofiles.open(file_path, "r", encoding="utf-8") as file:
        return await file.read()


def _is_standard_rmmz_filename(file_name: str) -> bool:
    """判断文件名是否属于标准 RMMZ 数据文件。"""
    return file_name in FIXED_FILE_NAMES or MAP_PATTERN.fullmatch(file_name) is not None


def _log_skipped_data_files(*, source_data_dir: Path, valid_files: list[Path]) -> None:
    """把未知 data JSON 文件记录到 DEBUG 日志。"""
    valid_names = {file_path.name for file_path in valid_files}
    for file_path in sorted(source_data_dir.glob("*.json"), key=lambda path: path.name):
        if file_path.name in valid_names:
            continue
        logger.debug(
            f"[tag.skip]跳过非标准 data 文件[/tag.skip] [tag.path]{file_path}[/tag.path]"
        )


def _decode_json_value(*, content: str, source: Path) -> JsonValue:
    """把 JSON 文本解析并校验为项目允许的 JSON 值。"""
    try:
        decoded = cast(object, json.loads(content))
        return coerce_json_value(decoded)
    except TypeError as error:
        raise TypeError(f"JSON 内容不是项目允许的值类型: {source}") from error


def _parse_plugins_js_text(plugins_content: str) -> list[dict[str, JsonValue]]:
    """从 `plugins.js` 解析 `$plugins` 数组。"""
    match = PLUGINS_JS_PATTERN.search(plugins_content)
    if match is None:
        raise ValueError("plugins.js 中未找到 `var $plugins = [...]` 标准结构")

    plugins_array_text = match.group(1)
    json_value = coerce_json_value(demjson3.decode(plugins_array_text))
    if not isinstance(json_value, list):
        raise ValueError("plugins.js 中的 `$plugins` 必须是数组")

    plugins: list[dict[str, JsonValue]] = []
    for index, plugin in enumerate(json_value):
        if not isinstance(plugin, dict):
            raise ValueError(f"plugins.js 第 {index} 个插件不是对象")
        plugins.append(plugin)
    return plugins


__all__: list[str] = [
    "GameDataManager",
    "load_game_data",
    "read_game_title",
    "resolve_data_source_file",
    "resolve_game_directory",
    "resolve_game_source_paths",
]
