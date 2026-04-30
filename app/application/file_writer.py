"""游戏文件回写编排。

首次写回按文件粒度备份本轮实际受影响的原始文件。后续写回直接替换激活版
文件，并保持原件留档不变。
"""

import copy
import json
import tempfile
from pathlib import Path

import shutil

from app.rmmz.schema import (
    DATA_DIRECTORY_NAME,
    DATA_ORIGIN_DIRECTORY_NAME,
    GameData,
    JS_DIRECTORY_NAME,
    PLUGINS_FILE_NAME,
    PLUGINS_ORIGIN_FILE_NAME,
)
from app.rmmz.text_rules import JsonValue


def reset_writable_copies(game_data: GameData) -> None:
    """重置游戏数据的可写副本，保证每次回写都从加载数据重新套用译文。"""
    game_data.writable_data = copy.deepcopy(game_data.data)
    game_data.writable_plugins_js = copy.deepcopy(game_data.plugins_js)


def write_game_files(game_data: GameData, game_root: Path) -> None:
    """把本轮受影响的游戏文件替换到激活版路径。"""
    active_data_dir, origin_data_dir, active_plugins_path, origin_plugins_path = build_game_layout_paths(game_root)
    changed_data_files = collect_changed_data_file_names(game_data)
    plugins_changed = is_plugins_file_changed(game_data)

    if not changed_data_files and not plugins_changed:
        return

    ensure_active_layout_exists(
        active_data_dir=active_data_dir,
        active_plugins_path=active_plugins_path,
    )
    has_existing_backup = origin_data_dir.exists() or origin_plugins_path.exists()
    if not has_existing_backup:
        backup_affected_original_files(
            changed_data_files=changed_data_files,
            active_data_dir=active_data_dir,
            origin_data_dir=origin_data_dir,
            plugins_changed=plugins_changed,
            active_plugins_path=active_plugins_path,
            origin_plugins_path=origin_plugins_path,
        )

    replace_changed_data_files(
        game_data=game_data,
        changed_data_files=changed_data_files,
        active_data_dir=active_data_dir,
        temp_dir=game_root,
    )
    if plugins_changed:
        plugins_content = game_data.writable_data[PLUGINS_FILE_NAME]
        replace_plugins_file(
            plugins_path=active_plugins_path,
            data=plugins_content,
            temp_dir=active_plugins_path.parent,
        )


def collect_changed_data_file_names(game_data: GameData) -> list[str]:
    """找出本轮相对加载源发生变化的标准 data 文件。"""
    changed_files: list[str] = []
    for file_name, writable_value in sorted(game_data.writable_data.items()):
        if file_name == PLUGINS_FILE_NAME:
            continue
        original_value = game_data.data.get(file_name)
        if writable_value != original_value:
            changed_files.append(file_name)
    return changed_files


def is_plugins_file_changed(game_data: GameData) -> bool:
    """判断本轮是否需要替换 `js/plugins.js`。"""
    writable_plugins = game_data.writable_data.get(PLUGINS_FILE_NAME)
    original_plugins = game_data.data.get(PLUGINS_FILE_NAME)
    return writable_plugins != original_plugins


def build_game_layout_paths(game_root: Path) -> tuple[Path, Path, Path, Path]:
    """构造当前游戏目录下激活版与原件备份路径。"""
    active_data_dir = game_root / DATA_DIRECTORY_NAME
    origin_data_dir = game_root / DATA_ORIGIN_DIRECTORY_NAME
    active_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_FILE_NAME
    origin_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_ORIGIN_FILE_NAME
    return active_data_dir, origin_data_dir, active_plugins_path, origin_plugins_path


def ensure_active_layout_exists(*, active_data_dir: Path, active_plugins_path: Path) -> None:
    """确认激活版数据目录和插件配置文件存在。"""
    if not active_data_dir.exists():
        raise FileNotFoundError(f"激活数据目录不存在: {active_data_dir}")
    if not active_plugins_path.exists():
        raise FileNotFoundError(f"激活插件配置文件不存在: {active_plugins_path}")


def backup_affected_original_files(
    *,
    changed_data_files: list[str],
    active_data_dir: Path,
    origin_data_dir: Path,
    plugins_changed: bool,
    active_plugins_path: Path,
    origin_plugins_path: Path,
) -> None:
    """首次写回前，只复制本轮受影响文件的原件留档。"""
    if changed_data_files:
        origin_data_dir.mkdir(parents=True, exist_ok=True)
        for file_name in changed_data_files:
            source_path = active_data_dir / file_name
            target_path = origin_data_dir / file_name
            if not source_path.exists():
                raise FileNotFoundError(f"待备份原始 data 文件不存在: {source_path}")
            _ = shutil.copy2(source_path, target_path)

    if plugins_changed:
        origin_plugins_path.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(active_plugins_path, origin_plugins_path)


def replace_changed_data_files(
    *,
    game_data: GameData,
    changed_data_files: list[str],
    active_data_dir: Path,
    temp_dir: Path,
) -> None:
    """把变化后的 data 文件逐个替换到激活版目录。"""
    for file_name in changed_data_files:
        target_path = active_data_dir / file_name
        data = game_data.writable_data[file_name]
        replace_json_file(target_path=target_path, data=data, temp_dir=temp_dir)


def replace_json_file(*, target_path: Path, data: JsonValue, temp_dir: Path) -> None:
    """用临时文件替换目标 JSON 文件。"""
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    replace_text_file(target_path=target_path, content=f"{payload}\n", temp_dir=temp_dir)


def replace_plugins_file(*, plugins_path: Path, data: JsonValue, temp_dir: Path) -> None:
    """替换激活版插件配置文件。"""
    if isinstance(data, str):
        replace_text_file(target_path=plugins_path, content=data, temp_dir=temp_dir)
        return
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    replace_text_file(target_path=plugins_path, content=f"{payload}\n", temp_dir=temp_dir)


def replace_text_file(*, target_path: Path, content: str, temp_dir: Path) -> None:
    """先写入临时文件，再用 `replace` 切换到目标路径。"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=target_path.suffix,
        prefix=f"{target_path.stem}_",
        dir=temp_dir,
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)
        _ = temp_file.write(content)

    try:
        _ = temp_path.replace(target_path)
    except Exception:
        cleanup_path(temp_path)
        raise


def cleanup_path(target_path: Path) -> None:
    """清理临时目录或临时文件。"""
    if target_path.is_dir():
        shutil.rmtree(target_path, ignore_errors=True)
    elif target_path.exists():
        target_path.unlink()


__all__: list[str] = [
    "collect_changed_data_file_names",
    "is_plugins_file_changed",
    "reset_writable_copies",
    "write_game_files",
]
