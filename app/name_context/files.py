"""标准名术语临时文件读写模块。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import aiofiles

from app.rmmz.schema import GameData

from .extraction import SPEAKER_SAMPLE_DIRECTORY_NAME, NameContextExtraction, build_speaker_sample_file_name
from .schemas import NameContextRegistry, SpeakerDialogueContext

REGISTRY_FILE_NAME = "name_registry.json"


@dataclass(frozen=True, slots=True)
class NameContextExportSummary:
    """标准名术语导出结果摘要。"""

    registry_path: Path
    sample_dir: Path
    entry_count: int
    speaker_entry_count: int
    map_entry_count: int
    sample_file_count: int


async def export_name_context_artifacts(
    *,
    game_data: GameData,
    output_dir: Path,
) -> NameContextExportSummary:
    """导出术语表和按名字聚合的对白样本。"""
    target_dir = output_dir.resolve()
    sample_dir = target_dir / SPEAKER_SAMPLE_DIRECTORY_NAME
    target_dir.mkdir(parents=True, exist_ok=True)
    sample_dir.mkdir(parents=True, exist_ok=True)

    registry, contexts = NameContextExtraction(game_data=game_data).extract_registry_and_contexts()
    registry_path = target_dir / REGISTRY_FILE_NAME
    await write_registry_json(registry_path, registry)
    sample_file_names: dict[str, str] = {}
    for context in contexts:
        sample_file_name = build_speaker_sample_file_name(context.name)
        previous_name = sample_file_names.get(sample_file_name)
        if previous_name is not None and previous_name != context.name:
            raise ValueError(
                f"对白样本文件名冲突: {previous_name} / {context.name} -> {sample_file_name}"
            )
        sample_file_names[sample_file_name] = context.name
        await write_context_json(sample_dir / sample_file_name, context)

    return NameContextExportSummary(
        registry_path=registry_path,
        sample_dir=sample_dir,
        entry_count=len(registry.speaker_names) + len(registry.map_display_names),
        speaker_entry_count=len(registry.speaker_names),
        map_entry_count=len(registry.map_display_names),
        sample_file_count=len(contexts),
    )


async def load_name_context_registry(*, registry_path: Path) -> NameContextRegistry:
    """读取外部 Agent 填写后的术语表。"""
    resolved_path = registry_path.resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"术语表导入文件不存在: {resolved_path}")

    async with aiofiles.open(resolved_path, "r", encoding="utf-8") as file:
        raw_text = await file.read()
    return NameContextRegistry.model_validate_json(raw_text)


async def write_registry_json(path: Path, registry: NameContextRegistry) -> None:
    """写入术语表 JSON。"""
    payload = json.dumps(registry.model_dump(mode="json"), ensure_ascii=False, indent=2)
    async with aiofiles.open(path, "w", encoding="utf-8") as file:
        _ = await file.write(f"{payload}\n")


async def write_context_json(path: Path, context: SpeakerDialogueContext) -> None:
    """写入单个名字的对白样本 JSON。"""
    payload = json.dumps(context.model_dump(mode="json"), ensure_ascii=False, indent=2)
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
    "export_name_context_artifacts",
    "load_name_context_registry",
    "sanitize_game_title_for_path",
    "write_context_json",
    "write_registry_json",
]
