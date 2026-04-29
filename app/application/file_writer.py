"""游戏文件原子回写编排。"""

import copy
import json
import shutil
import tempfile
from pathlib import Path

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
    """重置游戏数据的可写副本，保证每次回写都从原始数据重新套用译文。"""
    game_data.writable_data = copy.deepcopy(game_data.data)
    game_data.writable_plugins_js = copy.deepcopy(game_data.plugins_js)


def write_game_files(game_data: GameData, game_root: Path) -> None:
    """基于原件重建新的激活版 `data/` 与 `plugins.js`。"""
    js_dir = game_root / JS_DIRECTORY_NAME
    js_dir.mkdir(parents=True, exist_ok=True)
    active_data_dir, origin_data_dir, active_plugins_path, origin_plugins_path = build_game_layout_paths(game_root)
    has_origin_backup = validate_origin_backup_state(
        origin_data_dir=origin_data_dir,
        origin_plugins_path=origin_plugins_path,
    )
    staged_data_dir = Path(tempfile.mkdtemp(prefix="write_back_data_", dir=game_root))
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".js",
        prefix="write_back_plugins_",
        dir=js_dir,
        delete=False,
    ) as temp_plugins_file:
        staged_plugins_path = Path(temp_plugins_file.name)

    try:
        if not has_origin_backup:
            create_origin_backup(
                active_data_dir=active_data_dir,
                origin_data_dir=origin_data_dir,
                active_plugins_path=active_plugins_path,
                origin_plugins_path=origin_plugins_path,
            )
        stage_game_files(
            game_data=game_data,
            source_data_dir=origin_data_dir,
            source_plugins_path=origin_plugins_path,
            staged_data_dir=staged_data_dir,
            staged_plugins_path=staged_plugins_path,
        )
        if has_origin_backup:
            replace_active_layout(
                game_root=game_root,
                active_data_dir=active_data_dir,
                active_plugins_path=active_plugins_path,
                staged_data_dir=staged_data_dir,
                staged_plugins_path=staged_plugins_path,
            )
        else:
            create_active_layout_from_stage(
                active_data_dir=active_data_dir,
                active_plugins_path=active_plugins_path,
                staged_data_dir=staged_data_dir,
                staged_plugins_path=staged_plugins_path,
            )
    except Exception:
        if not has_origin_backup:
            rollback_initial_backup_failure(
                active_data_dir=active_data_dir,
                origin_data_dir=origin_data_dir,
                active_plugins_path=active_plugins_path,
                origin_plugins_path=origin_plugins_path,
            )
        raise
    finally:
        cleanup_path(staged_data_dir)
        cleanup_path(staged_plugins_path)


def stage_game_files(
    game_data: GameData,
    source_data_dir: Path,
    source_plugins_path: Path,
    staged_data_dir: Path,
    staged_plugins_path: Path,
) -> None:
    """基于原件生成一份可供切换的激活版临时目录。"""
    shutil.rmtree(staged_data_dir, ignore_errors=True)
    _ = shutil.copytree(source_data_dir, staged_data_dir)
    _ = shutil.copy2(source_plugins_path, staged_plugins_path)
    for file_name, data in game_data.writable_data.items():
        if file_name == PLUGINS_FILE_NAME:
            write_plugins_file(staged_plugins_path, data)
            continue
        target_path = staged_data_dir / file_name
        _ = target_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_game_layout_paths(game_root: Path) -> tuple[Path, Path, Path, Path]:
    """构造当前游戏目录下激活版与原件备份路径。"""
    active_data_dir = game_root / DATA_DIRECTORY_NAME
    origin_data_dir = game_root / DATA_ORIGIN_DIRECTORY_NAME
    active_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_FILE_NAME
    origin_plugins_path = game_root / JS_DIRECTORY_NAME / PLUGINS_ORIGIN_FILE_NAME
    return active_data_dir, origin_data_dir, active_plugins_path, origin_plugins_path


def validate_origin_backup_state(origin_data_dir: Path, origin_plugins_path: Path) -> bool:
    """校验原件备份是否处于一致状态。"""
    has_origin_data_dir = origin_data_dir.exists()
    has_origin_plugins_path = origin_plugins_path.exists()
    if has_origin_data_dir != has_origin_plugins_path:
        raise ValueError("检测到半成品翻译布局：`data_origin/` 与 `js/plugins_origin.js` 必须同时存在或同时不存在")
    return has_origin_data_dir


def create_origin_backup(
    active_data_dir: Path,
    origin_data_dir: Path,
    active_plugins_path: Path,
    origin_plugins_path: Path,
) -> None:
    """首次回写前，将当前激活版目录重命名为原件备份。"""
    if not active_data_dir.exists():
        raise FileNotFoundError(f"激活数据目录不存在: {active_data_dir}")
    if not active_plugins_path.exists():
        raise FileNotFoundError(f"激活插件配置文件不存在: {active_plugins_path}")
    _ = active_data_dir.rename(origin_data_dir)
    try:
        _ = active_plugins_path.rename(origin_plugins_path)
    except Exception:
        _ = origin_data_dir.rename(active_data_dir)
        raise


def create_active_layout_from_stage(
    active_data_dir: Path,
    active_plugins_path: Path,
    staged_data_dir: Path,
    staged_plugins_path: Path,
) -> None:
    """首次备份完成后，把临时激活版写回默认运行路径。"""
    _ = staged_data_dir.rename(active_data_dir)
    try:
        _ = staged_plugins_path.replace(active_plugins_path)
    except Exception:
        shutil.rmtree(active_data_dir, ignore_errors=True)
        raise


def replace_active_layout(
    game_root: Path,
    active_data_dir: Path,
    active_plugins_path: Path,
    staged_data_dir: Path,
    staged_plugins_path: Path,
) -> None:
    """在已翻译布局上重新生成新的激活版目录。"""
    rollback_root = Path(tempfile.mkdtemp(prefix="write_back_rollback_", dir=game_root))
    rollback_data_dir = rollback_root / "data"
    rollback_plugins_path = rollback_root / PLUGINS_FILE_NAME
    data_swapped = False
    plugins_swapped = False
    try:
        if active_data_dir.exists():
            _ = active_data_dir.rename(rollback_data_dir)
        _ = staged_data_dir.rename(active_data_dir)
        data_swapped = True
        if active_plugins_path.exists():
            _ = active_plugins_path.rename(rollback_plugins_path)
        _ = staged_plugins_path.replace(active_plugins_path)
        plugins_swapped = True
    except Exception:
        if data_swapped and active_data_dir.exists():
            shutil.rmtree(active_data_dir, ignore_errors=True)
        if rollback_data_dir.exists():
            _ = rollback_data_dir.rename(active_data_dir)
        if plugins_swapped and active_plugins_path.exists():
            active_plugins_path.unlink()
        if rollback_plugins_path.exists():
            _ = rollback_plugins_path.rename(active_plugins_path)
        raise
    finally:
        shutil.rmtree(rollback_root, ignore_errors=True)


def rollback_initial_backup_failure(
    active_data_dir: Path,
    origin_data_dir: Path,
    active_plugins_path: Path,
    origin_plugins_path: Path,
) -> None:
    """首次回写失败后，把原件备份恢复回默认运行路径。"""
    if active_data_dir.exists():
        shutil.rmtree(active_data_dir, ignore_errors=True)
    if active_plugins_path.exists():
        active_plugins_path.unlink()
    if origin_data_dir.exists():
        _ = origin_data_dir.rename(active_data_dir)
    if origin_plugins_path.exists():
        _ = origin_plugins_path.rename(active_plugins_path)


def write_plugins_file(plugins_path: Path, data: JsonValue) -> None:
    """将插件配置文本写入目标文件。"""
    if isinstance(data, str):
        _ = plugins_path.write_text(data, encoding="utf-8")
        return
    _ = plugins_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cleanup_path(target_path: Path) -> None:
    """清理临时目录或临时文件。"""
    if target_path.is_dir():
        shutil.rmtree(target_path, ignore_errors=True)
    elif target_path.exists():
        target_path.unlink()


__all__: list[str] = [
    "reset_writable_copies",
    "write_game_files",
]
