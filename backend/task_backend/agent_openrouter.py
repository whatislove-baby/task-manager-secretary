"""Narrow streaming compatibility shim for the production Agent provider.

langchain-openrouter 0.3.0 / langchain-core 1.8.4 concatenate a repeated
``reasoning_details[i].format`` string while adding AIMessageChunks. Format is
whole-block metadata, not a token delta. Keep its first occurrence per index,
remove only identical repetitions, and reject conflicting values. Everything
else, including text, encrypted data, signatures and tool deltas, passes through.

No third-party files are modified. Non-streaming conversion and the agent/tool
loop remain upstream. This does not establish live-provider compatibility.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

from httpx import ConnectError, RemoteProtocolError
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openrouter import ChatOpenRouter


class ReasoningMetadataConflict(ValueError):
    """A stream changed metadata for an existing reasoning block."""


def _preserve_format(
    chunk: ChatGenerationChunk, formats: dict[int, str]
) -> ChatGenerationChunk:
    details = chunk.message.additional_kwargs.get("reasoning_details")
    if not isinstance(details, list):
        return chunk

    changed = False
    normalized = []
    for detail in details:
        # Without an index two objects may be separate blocks. Do not guess.
        if not isinstance(detail, dict) or type(detail.get("index")) is not int:
            normalized.append(detail)
            continue
        index = detail["index"]
        value = detail.get("format")
        if value is None:
            normalized.append(detail)
            continue
        if not isinstance(value, str):
            raise ReasoningMetadataConflict("Invalid reasoning format metadata")
        if index not in formats:
            formats[index] = value
            normalized.append(detail)
        elif formats[index] != value:
            # Do not include the provider's payload in the error or try to infer
            # which format was intended. The caller can report an incomplete run.
            raise ReasoningMetadataConflict(
                f"Conflicting reasoning format metadata for block {index}"
            )
        else:
            normalized.append({key: item for key, item in detail.items() if key != "format"})
            changed = True

    if not changed:
        return chunk
    message = chunk.message.model_copy(
        update={"additional_kwargs": {**chunk.message.additional_kwargs, "reasoning_details": normalized}}
    )
    return chunk.model_copy(update={"message": message})


class FormatPreservingChatOpenRouter(ChatOpenRouter):
    """Preserve format metadata; retry one transport failure before any chunk.

    Even a blank, usage-only or tool-delta chunk commits the stream. Retrying
    after that boundary could join two generations or replay a tool, so it is
    forbidden. This shim does not retry provider/application errors or cancel.
    """

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        for attempt in range(2):
            formats: dict[int, str] = {}
            received_chunk = False
            try:
                for chunk in super()._stream(messages, stop=stop, run_manager=None, **kwargs):
                    received_chunk = True
                    normalized = _preserve_format(chunk, formats)
                    if run_manager:
                        run_manager.on_llm_new_token(
                            normalized.text,
                            chunk=normalized,
                            logprobs=(normalized.generation_info or {}).get("logprobs"),
                        )
                    yield normalized
                return
            except (RemoteProtocolError, ConnectError):
                if received_chunk or attempt:
                    raise
                time.sleep(0.25)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for attempt in range(2):
            formats: dict[int, str] = {}
            received_chunk = False
            try:
                async for chunk in super()._astream(messages, stop=stop, run_manager=None, **kwargs):
                    received_chunk = True
                    normalized = _preserve_format(chunk, formats)
                    if run_manager:
                        await run_manager.on_llm_new_token(
                            normalized.text,
                            chunk=normalized,
                            logprobs=(normalized.generation_info or {}).get("logprobs"),
                        )
                    yield normalized
                return
            except (RemoteProtocolError, ConnectError):
                if received_chunk or attempt:
                    raise
                # CancelledError during the wait propagates immediately. Never
                # start a second request after the user has cancelled the run.
                await asyncio.sleep(0.25)
