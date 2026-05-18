"""MV 正文说话人解析工具。"""

import re
from dataclasses import dataclass

from app.rmmz.schema import GameData


ACTOR_NAME_PREFIX_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<control>\\N\[(?P<actor_id>\d+)\])\s*[:：]",
)
YEP_NAME_BOX_PATTERN: re.Pattern[str] = re.compile(
    r"^\\n(?:c|r)?<(?P<speaker>[^>\r\n]{1,80})>",
)
DARK_PLASMA_AUTO_NAME_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<speaker>[^\\「（:：\r\n]{1,40})\s*[:：]?[「（]"
)
STANDALONE_SPEAKER_LINE_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<speaker>[^\\「『【\[\]()（）:：\r\n]{1,40})\s*[:：]\s*$"
)


@dataclass(frozen=True, slots=True)
class MvSpeakerParseResult:
    """MV 正文首行说话人解析结果。"""

    speaker: str
    source: str


def parse_mv_speaker_from_first_text(
    *,
    text: str,
    game_data: GameData,
) -> MvSpeakerParseResult | None:
    """从 MV `401` 第一行解析当前对白的说话人。"""
    normalized_text = text.strip()
    if not normalized_text:
        return None

    actor_match = ACTOR_NAME_PREFIX_PATTERN.match(normalized_text)
    if actor_match is not None:
        actor_id = int(actor_match.group("actor_id"))
        actor_name = _actor_name_by_id(game_data=game_data, actor_id=actor_id)
        if actor_name is not None:
            return MvSpeakerParseResult(speaker=actor_name, source="actor_name_control")
        return MvSpeakerParseResult(
            speaker=actor_match.group("control"),
            source="actor_name_control",
        )

    yep_match = YEP_NAME_BOX_PATTERN.match(normalized_text)
    if yep_match is not None:
        speaker = _clean_speaker_text(yep_match.group("speaker"))
        if speaker:
            return MvSpeakerParseResult(speaker=speaker, source="yep_name_box")

    dark_plasma_match = DARK_PLASMA_AUTO_NAME_PATTERN.match(normalized_text)
    if dark_plasma_match is not None:
        speaker = _clean_speaker_text(dark_plasma_match.group("speaker"))
        if speaker:
            return MvSpeakerParseResult(speaker=speaker, source="dark_plasma_auto_name")

    standalone_match = STANDALONE_SPEAKER_LINE_PATTERN.match(normalized_text)
    if standalone_match is not None:
        speaker = _clean_speaker_text(standalone_match.group("speaker"))
        if speaker:
            return MvSpeakerParseResult(speaker=speaker, source="standalone_speaker_line")

    return None


def _actor_name_by_id(*, game_data: GameData, actor_id: int) -> str | None:
    """按数据库角色 ID 读取角色名。"""
    for actor in game_data.base_data.get("Actors.json", []):
        if actor is None or actor.id != actor_id:
            continue
        actor_name = actor.name.strip()
        if actor_name:
            return actor_name
    return None


def _clean_speaker_text(text: str) -> str:
    """清理说话人文本外侧空白。"""
    return text.strip()


__all__: list[str] = [
    "MvSpeakerParseResult",
    "parse_mv_speaker_from_first_text",
]
