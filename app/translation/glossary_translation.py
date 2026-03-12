"""
术语翻译模块。

负责处理术语提取层输出的角色样本与地点名，
并把模型响应解析为结构化的 `Role` 与 `Place` 对象。

边界说明：
1. 这里负责术语分块、提示词拼装、模型响应修复与结构校验。
2. 这里不负责数据库写入，也不负责正文翻译上下文构建。
3. 这里保留逐块流式产出语义，供 `TranslationHandler.build_glossary()` 统一编排。
"""

from collections.abc import AsyncIterator
import json
from itertools import batched
from typing import Any, Literal, Self

from json_repair import repair_json
from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from app.config import Setting
from app.models.schemas import Place, Role
from app.services.llm.handler import LLMHandler
from app.services.llm.schemas import ChatMessage


class RoleTranslationValue(BaseModel):
    """
    角色术语大模型返回结果值模型。

    约束大模型在进行角色名翻译时，返回 JSON 对象中每个 value 的结构。
    使用别名 `alias` 是因为 Prompt 中要求大模型输出中文 key 以保证理解准确性，
    但在内部代码流转中统一使用英文变量。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    translated_name: str = Field(alias="译名")
    gender: Literal["男", "女", "未知"] = Field(alias="性别")


class PlaceTranslationValue(BaseModel):
    """
    地点术语大模型返回结果值模型。

    约束大模型在进行地点翻译时，每个地点对应的值应仅包含“译名”。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    translated_name: str = Field(alias="译名")


class GlossaryTranslation:
    """
    术语翻译服务类。

    该类负责将前期提取到的“角色名+样例台词”以及“地图名称”组织成 Prompt 发送给 LLM 进行翻译。
    为了避免超长，采用了分批（Batched）请求。
    此外，它包含了对模型返回的 JSON 结构进行严格校验以及自动携带错误信息重试的机制。
    """

    def __init__(self, setting: Setting) -> None:
        """
        初始化术语翻译服务。

        Args:
            setting: 项目的全局运行时配置。
        """
        self.setting: Setting = setting

    async def translate_role_names(
        self,
        llm_handler: LLMHandler,
        role_lines: dict[str, list[str]],
    ) -> AsyncIterator[list[Role]]:
        """
        执行角色术语的分块翻译，并以流式的形式不断抛出验证完成的结构化结果。

        Args:
            llm_handler: LLM 服务调用器，用于实际发起网络请求。
            role_lines: 从游戏内提取的未翻译角色名及其样本对话台词字典。

        Yields:
            每次成功翻译并校验通过的一个角色术语批次（`list[Role]`）。
            
        Raises:
            ValueError: 在达到设定的最大结构重试次数后，大模型仍未能返回完全符合要求的 JSON 格式数据时抛出。
        """
        if not role_lines:
            return

        task_setting = self.setting.glossary_translation.role_name
        llm_setting = self.setting.llm_services.glossary
        messages: list[ChatMessage] = [
            ChatMessage(role="system", text=task_setting.system_prompt)
        ]

        for chunk_index, chunk in enumerate(
            batched(role_lines.items(), task_setting.chunk_size),
            start=1,
        ):
            chunk_dict: dict[str, list[str]] = dict(chunk)
            messages.append(
                ChatMessage(
                    role="user",
                    text=json.dumps(chunk_dict, ensure_ascii=False, indent=2),
                )
            )

            validator = self._create_role_translation_validator(chunk_dict)
            response_attempt: int = 0

            while True:
                try:
                    translation_mapping = await self._request_translation_chunk(
                        llm_handler=llm_handler,
                        messages=messages,
                        translation_validator=validator,
                        service_name="glossary",
                        model=llm_setting.model,
                        retry_count=task_setting.retry_count,
                        retry_delay=task_setting.retry_delay,
                    )
                    yield [
                        Role(
                            name=name,
                            translated_name=value.translated_name,
                            gender=value.gender,
                        )
                        for name, value in translation_mapping.items()
                    ]
                    break
                except Exception as error:
                    response_attempt += 1
                    if response_attempt >= task_setting.response_retry_count:
                        raise ValueError(
                            f"角色术语第 {chunk_index} 批翻译失败：{error}"
                        ) from error
                    messages.append(self._build_retry_message(error=error))

    async def translate_display_names(
        self,
        llm_handler: LLMHandler,
        display_names: dict[str, str],
        roles: list[Role],
    ) -> AsyncIterator[list[Place]]:
        """
        执行地点（地图）术语的分块翻译。

        为了让大模型在翻译地点名时能够获得语境（防止与某些人名混淆），
        这个方法会将被该批次地点名“命中”（即地图名中可能包含角色名的情况）的已翻译角色术语
        作为参考上下文，一并传给大模型。

        Args:
            llm_handler: LLM 服务调用器。
            display_names: 提取出的待翻译地点名词典（值为空占位）。
            roles: 已经在上一步翻译完成的完整角色列表。

        Yields:
            每次成功翻译并校验通过的地点术语批次（`list[Place]`）。
            
        Raises:
            ValueError: 多次重试均未能通过结构或业务校验时抛出。
        """
        if not display_names:
            return

        task_setting = self.setting.glossary_translation.display_name
        llm_setting = self.setting.llm_services.glossary
        messages: list[ChatMessage] = [
            ChatMessage(role="system", text=task_setting.system_prompt)
        ]

        for chunk_index, chunk in enumerate(
            batched(display_names.items(), task_setting.chunk_size),
            start=1,
        ):
            chunk_dict: dict[str, str] = dict(chunk)
            hit_roles: list[Role] = self._collect_hit_roles(
                display_name_chunk=chunk_dict,
                roles=roles,
            )

            user_message_lines: list[str] = []
            if hit_roles:
                user_message_lines.extend(
                    [
                        "命中的角色术语：",
                        json.dumps(
                            [
                                {
                                    "原名": role.name,
                                    "译名": role.translated_name,
                                    "性别": role.gender,
                                }
                                for role in hit_roles
                            ],
                            ensure_ascii=False,
                            indent=2,
                        ),
                    ]
                )
            user_message_lines.extend(
                [
                    "需要翻译的地点术语：",
                    json.dumps(chunk_dict, ensure_ascii=False, indent=2),
                ]
            )
            messages.append(
                ChatMessage(role="user", text="\n".join(user_message_lines))
            )

            validator = self._create_place_translation_validator(chunk_dict)
            response_attempt: int = 0

            while True:
                try:
                    translation_mapping = await self._request_translation_chunk(
                        llm_handler=llm_handler,
                        messages=messages,
                        translation_validator=validator,
                        service_name="glossary",
                        model=llm_setting.model,
                        retry_count=task_setting.retry_count,
                        retry_delay=task_setting.retry_delay,
                    )
                    yield [
                        Place(name=name, translated_name=value.translated_name)
                        for name, value in translation_mapping.items()
                    ]
                    break
                except Exception as error:
                    response_attempt += 1
                    if response_attempt >= task_setting.response_retry_count:
                        raise ValueError(
                            f"地点术语第 {chunk_index} 批翻译失败：{error}"
                        ) from error
                    messages.append(self._build_retry_message(error=error))

    @staticmethod
    def _create_role_translation_validator(
        data: dict[str, list[str]],
    ) -> type[RootModel[dict[str, RoleTranslationValue]]]:
        """
        动态创建一个针对当前批次的 Pydantic 响应校验模型。

        为什么这样做：
        为了严格保证大模型没有“漏翻”或“自作主张地多翻”，这里的校验器会在运行时
        绑定当前发送过去的具体 Key（原名）集合。

        Args:
            data: 当前即将发送给大模型的源数据块字典。

        Returns:
            动态生成的能够验证字典 Key 一致性与 Value 非空的 RootModel 类型。
        """
        expected_keys: list[str] = list(data.keys())
        expected_key_set: set[str] = set(expected_keys)

        class RoleTranslationResultModel(RootModel[dict[str, RoleTranslationValue]]):
            """
            角色术语响应校验模型。

            负责校验 key 集合完整性与字段非空约束。
            """

            @model_validator(mode="after")
            def validate_keys_and_values(self) -> Self:
                """
                校验角色术语响应结构是否完整。

                Returns:
                    当前响应模型自身。
                """
                received_keys: set[str] = set(self.root.keys())
                missing_keys: set[str] = expected_key_set - received_keys
                extra_keys: set[str] = received_keys - expected_key_set

                if missing_keys:
                    raise ValueError(f"漏翻了以下角色术语：{sorted(missing_keys)}")
                if extra_keys:
                    raise ValueError(f"返回了未请求的角色术语：{sorted(extra_keys)}")

                empty_keys: list[str] = [
                    key
                    for key, value in self.root.items()
                    if not value.translated_name.strip()
                ]
                if empty_keys:
                    raise ValueError(f"以下角色术语译名为空：{empty_keys}")

                return self

        return RoleTranslationResultModel

    @staticmethod
    def _create_place_translation_validator(
        data: dict[str, str],
    ) -> type[RootModel[dict[str, PlaceTranslationValue]]]:
        """
        创建地点术语响应校验器。

        Args:
            data: 当前地点术语分块原始输入。

        Returns:
            可用于 `model_validate_json` 的动态 RootModel 子类。
        """
        expected_key_set: set[str] = set(data.keys())

        class PlaceTranslationResultModel(RootModel[dict[str, PlaceTranslationValue]]):
            """
            地点术语响应校验模型。

            负责校验 key 集合完整性与字段非空约束。
            """

            @model_validator(mode="after")
            def validate_keys_and_values(self) -> Self:
                """
                校验地点术语响应结构是否完整。

                Returns:
                    当前响应模型自身。
                """
                received_keys: set[str] = set(self.root.keys())
                missing_keys: set[str] = expected_key_set - received_keys
                extra_keys: set[str] = received_keys - expected_key_set

                if missing_keys:
                    raise ValueError(f"漏翻了以下地点术语：{sorted(missing_keys)}")
                if extra_keys:
                    raise ValueError(f"返回了未请求的地点术语：{sorted(extra_keys)}")

                empty_keys: list[str] = [
                    key
                    for key, value in self.root.items()
                    if not value.translated_name.strip()
                ]
                if empty_keys:
                    raise ValueError(f"以下地点术语译名为空：{empty_keys}")

                return self

        return PlaceTranslationResultModel

    async def _request_translation_chunk(
        self,
        llm_handler: LLMHandler,
        messages: list[ChatMessage],
        translation_validator: type[RootModel[Any]],
        service_name: str,
        model: str,
        retry_count: int,
        retry_delay: int,
    ) -> dict[str, Any]:
        """
        向大模型发起当前对话历史的请求，并对返回结果进行 JSON 修复与动态模型校验。

        由于大模型的输出不可控，可能会返回包裹在 Markdown Code Block 里的 JSON，
        或者多出无关的闲聊废话。此方法利用 `repair_json` 提取并修复文本，
        随后将其喂给传入的 Pydantic 动态模型进行业务逻辑层面的严格校验。

        Args:
            llm_handler: LLM 调用控制器。
            messages: 当前对话的所有上下文（可能包含上一次失败后的报错纠正信息）。
            translation_validator: 用于拦截并验证结果完整性的动态 RootModel。
            service_name: 调用的服务名称（如 glossary）。
            model: 调用的具体大模型。
            retry_count: 基础的网络重试参数。
            retry_delay: 基础的网络重试参数。

        Returns:
            验证通过并且干净的 Python 字典数据。
        """
        result: str = await llm_handler.get_ai_response(
            service_name=service_name,
            model=model,
            messages=messages,
            retry_count=retry_count,
            retry_delay=retry_delay,
        )

        repaired_result = repair_json(result, return_objects=False)
        if isinstance(repaired_result, tuple):
            repaired_value = repaired_result[0]
        else:
            repaired_value = repaired_result

        if isinstance(repaired_value, str):
            clean_result: str = repaired_value
        else:
            clean_result = json.dumps(repaired_value, ensure_ascii=False)

        messages.append(ChatMessage(role="assistant", text=clean_result))
        validated_result = translation_validator.model_validate_json(clean_result)
        return validated_result.root

    def _collect_hit_roles(
        self,
        display_name_chunk: dict[str, str],
        roles: list[Role],
    ) -> list[Role]:
        """
        提取当前地点术语分块命中的角色术语对象。

        Args:
            display_name_chunk: 当前地点术语分块。
            roles: 已完成的角色术语对象列表。

        Returns:
            当前分块命中的角色术语对象列表。
        """
        current_display_names: list[str] = list(display_name_chunk.keys())
        return [
            role
            for role in roles
            if any(role.name in display_name for display_name in current_display_names)
        ]

    def _build_retry_message(self, error: Exception) -> ChatMessage:
        """
        构造用于发给大模型的纠错提示消息。

        如果大模型由于多翻、漏翻或者字段缺失被本地 Validator 拦截，此函数
        会将报错信息原样发回给大模型，促使它自我反省并输出正确结果。

        Args:
            error: 在尝试解析或者 Pydantic 校验时抛出的具体异常。

        Returns:
            一条新的包含错误详情和重试指令的用户消息。
        """
        message_text: str = f"发生错误，详情如下：\n{error}\n请严格按要求重新翻译。"
        return ChatMessage(role="user", text=message_text)


__all__: list[str] = ["GlossaryTranslation"]
