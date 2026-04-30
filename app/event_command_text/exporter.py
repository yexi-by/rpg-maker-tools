"""事件指令参数 JSON 导出模块。"""

import json
from pathlib import Path

import aiofiles

from app.rmmz.commands import iter_all_commands
from app.rmmz.schema import GameData
from app.rmmz.text_rules import JsonValue


def resolve_event_command_codes(
    *,
    command_codes: set[int] | None,
    default_command_codes: list[int] | None,
) -> frozenset[int]:
    """解析事件指令参数导出的有效编码集合。"""
    if command_codes is None:
        if default_command_codes is None:
            raise ValueError("未传入 CLI 编码时必须提供配置文件默认编码数组")
        effective_codes = frozenset(default_command_codes)
    else:
        effective_codes = frozenset(command_codes)

    if not effective_codes:
        raise ValueError("事件指令导出编码不能为空")
    return effective_codes


async def export_event_commands_json_file(
    *,
    game_data: GameData,
    output_path: Path,
    command_codes: frozenset[int],
) -> int:
    """把指定事件指令编码的参数样本导出为 JSON 文件。"""
    resolved_output_path = output_path.resolve()
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    samples_by_code: dict[str, list[list[JsonValue]]] = {str(code): [] for code in sorted(command_codes)}
    seen_samples: set[tuple[int, str]] = set()

    for _path, _display_name, command in iter_all_commands(game_data):
        if command.code not in command_codes:
            continue
        sample_key = json.dumps(command.parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        dedupe_key = (command.code, sample_key)
        if dedupe_key in seen_samples:
            continue
        seen_samples.add(dedupe_key)
        samples_by_code[str(command.code)].append(command.parameters)

    async with aiofiles.open(resolved_output_path, "w", encoding="utf-8") as file:
        _ = await file.write(f"{json.dumps(samples_by_code, ensure_ascii=False, indent=2)}\n")
    return sum(len(samples) for samples in samples_by_code.values())


__all__: list[str] = [
    "export_event_commands_json_file",
    "resolve_event_command_codes",
]
