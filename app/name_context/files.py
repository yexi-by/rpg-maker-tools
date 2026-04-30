"""标准名上下文临时文件读写模块。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import aiofiles

from app.rmmz.schema import GameData

from .extraction import CONTEXT_DIRECTORY_NAME, NameContextExtraction
from .schemas import NameContextRegistry, SpeakerDialogueContext

REGISTRY_FILE_NAME = "name_registry.json"


@dataclass(frozen=True, slots=True)
class NameContextExportSummary:
    """标准名上下文导出结果摘要。"""

    registry_path: Path
    context_dir: Path
    entry_count: int
    speaker_entry_count: int
    map_entry_count: int
    context_file_count: int


async def export_name_context_files(
    *,
    game_title: str,
    game_data: GameData,
    output_dir: Path,
) -> NameContextExportSummary:
    """导出大 JSON 注册表和每个 `101` 的小 JSON 对话上下文。"""
    target_dir = output_dir.resolve()
    context_dir = target_dir / CONTEXT_DIRECTORY_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)

    registry, contexts = NameContextExtraction(
        game_title=game_title,
        game_data=game_data,
    ).extract_registry_and_contexts()
    registry_path = target_dir / REGISTRY_FILE_NAME
    await write_model_json(registry_path, registry)
    for context in contexts:
        await write_model_json(context_dir / f"{context.entry_id}.json", context)

    speaker_count = sum(1 for entry in registry.entries if entry.kind == "speaker_name")
    map_count = sum(1 for entry in registry.entries if entry.kind == "map_display_name")
    return NameContextExportSummary(
        registry_path=registry_path,
        context_dir=context_dir,
        entry_count=len(registry.entries),
        speaker_entry_count=speaker_count,
        map_entry_count=map_count,
        context_file_count=len(contexts),
    )


async def load_name_context_registry(*, registry_path: Path) -> NameContextRegistry:
    """读取外部 Agent 填写后的大 JSON 注册表。"""
    resolved_path = registry_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"术语表导入文件不存在: {resolved_path}")

    async with aiofiles.open(resolved_path, "r", encoding="utf-8") as file:
        raw_text = await file.read()
    registry = NameContextRegistry.model_validate_json(raw_text)
    return registry


async def write_model_json(path: Path, model: NameContextRegistry | SpeakerDialogueContext) -> None:
    """以统一格式写入 Pydantic 模型 JSON。"""
    payload = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)
    async with aiofiles.open(path, "w", encoding="utf-8") as file:
        _ = await file.write(f"{payload}\n")


def sanitize_game_title_for_path(game_title: str) -> str:
    """把游戏标题转换为稳定且适合目录名的片段。"""
    normalized = re.sub(r"[<>:\"/\\|?*\s]+", "_", game_title.strip()).strip("._")
    if normalized:
        return normalized
    return "untitled_game"


__all__: list[str] = [
    "REGISTRY_FILE_NAME",
    "NameContextExportSummary",
    "export_name_context_files",
    "load_name_context_registry",
    "sanitize_game_title_for_path",
    "write_model_json",
]
