"""Plain chat SDK streaming in the Fleet SSE format."""

from __future__ import annotations

import asyncio
import json
import select
import socket
from collections.abc import Iterator
from typing import Any

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, RunConfig, Runner
from agents.exceptions import AgentsException
from openai import APIError
from openai.types.completion_usage import CompletionUsage

from poolhouse.client import completion
from poolhouse.client.sdk import local_client
from poolhouse.http import ServerError

from .chat import ChatError, Target
from .framing import MOST_BODY

_STOP = object()
_DONE = b"data: [DONE]\n\n"


def _disconnected(connection: Any) -> bool:
    if connection is None or not select.select([connection], [], [], 0)[0]:
        return False
    return not connection.recv(1, socket.MSG_PEEK)


def _cancelled(connection, control):
    return _disconnected(connection) or (control is not None and control.cancelled.is_set())


async def _await_result(waiting, client, connection, control, cancel=None):
    pending = asyncio.ensure_future(waiting)
    try:
        while True:
            if _cancelled(connection, control):
                await client.close()
                if cancel is not None:
                    cancel()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                return _STOP
            if pending.done():
                return pending.result()
            await asyncio.wait([pending], timeout=0.1)
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def _events(target: Any, payload: dict[str, Any], connection: Any, control: Any = None):
    client = local_client(target, payload["model"])
    agent = Agent(name="Chat", model=OpenAIChatCompletionsModel(payload["model"], client),
                  model_settings=ModelSettings(temperature=payload.get("temperature"),
                                               max_tokens=payload.get("max_output_tokens")))
    result = Runner.run_streamed(agent, input=payload["messages"],
                                 run_config=RunConfig(tracing_disabled=True,
                                                      trace_include_sensitive_data=False))
    try:
        events = result.stream_events()
        while True:
            try:
                event = await _await_result(anext(events), client, connection, control, result.cancel)
            except StopAsyncIteration:
                break
            if event is _STOP:
                return
            if event.type == "raw_response_event":
                field = {"response.output_text.delta": "content",
                         "response.reasoning_summary_text.delta": "reasoning_content",
                         "response.reasoning_text.delta": "reasoning_content"}.get(event.data.type)
                if field is not None:
                    frame = {"choices": [{"delta": {field: event.data.delta}}]}
                    yield b"data: " + json.dumps(frame).encode() + b"\n\n"
        yield b"data: [DONE]\n\n"
    finally:
        result.cancel()
        await client.close()


def _bridge(events, *, defer_done=False) -> Iterator[bytes]:
    loop = asyncio.new_event_loop()
    done = False
    try:
        while True:
            try:
                block = loop.run_until_complete(anext(events))
                if defer_done and block == _DONE:
                    done = True
                else:
                    yield block
            except StopAsyncIteration:
                break
    except (AgentsException, APIError, ServerError, OSError, ValueError) as exc:
        raise ChatError(str(exc)) from exc
    finally:
        try:
            loop.run_until_complete(events.aclose())
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    try:
                        loop.run_until_complete(loop.shutdown_default_executor())
                    finally:
                        loop.close()
    if done:
        yield _DONE


def stream(target: Any, payload: dict[str, Any], *, connection: Any = None,
           control: Any = None) -> Iterator[bytes]:
    """Run one SDK turn and close generation when its consumer disconnects."""
    yield from _bridge(_events(target, payload, connection, control))


_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_FINISH = {"stop", "length", "tool_calls", "function_call", "content_filter"}
_FIELDS = {"model", "messages", "tools", "tool_choice", "parallel_tool_calls", "max_tokens", "max_completion_tokens",
           "temperature", "top_p", "stop", "seed", "presence_penalty", "frequency_penalty", "reasoning_effort",
           "response_format", "stream_options", "n", "stream", "store", "prompt_cache_key", "prompt_cache_retention",
           "chat_template_kwargs"}


def _completion_arguments(target, payload):
    if not isinstance(target, Target) or not isinstance(payload, dict) or set(payload) - _FIELDS:
        raise ValueError("Use the fixed native completion fields and selected target")
    encoded = json.dumps(payload, allow_nan=False)
    if len(encoded.encode()) > MOST_BODY:
        raise ValueError("Native completion input exceeds its byte limit")
    arguments = json.loads(encoded)
    if (arguments != payload or type(target.model) is not str or not target.model
            or arguments.get("model") != target.model):
        raise ValueError("Native completion must use its selected model and literal JSON")
    if (not isinstance(arguments.get("messages"), list) or not arguments["messages"]
            or any(not isinstance(message, dict) or message.get("role") not in
                   {"developer", "system", "user", "assistant", "tool", "function"} for message in arguments["messages"])):
        raise ValueError("Native completion messages must be typed message objects")
    for key, expected in (("stream", True), ("store", False)):
        if key in arguments and arguments[key] is not expected:
            raise ValueError("Native completions stream without storing")
    if "n" in arguments and (type(arguments["n"]) is not int or arguments["n"] != 1):
        raise ValueError("Native completions require exactly one choice")
    if "max_tokens" in arguments and "max_completion_tokens" in arguments:
        raise ValueError("Choose one native completion token field")
    for key in ("max_tokens", "max_completion_tokens", "seed"):
        if key in arguments and (type(arguments[key]) is not int or (key != "seed" and arguments[key] < 1)):
            raise ValueError("Native completion token counts and seed must be integers")
    for key, lower, upper in (("temperature", 0, 2), ("top_p", 0, 1),
                              ("presence_penalty", -2, 2), ("frequency_penalty", -2, 2)):
        if key in arguments and (type(arguments[key]) not in (float, int) or not lower <= arguments[key] <= upper):
            raise ValueError("Native completion sampling value is outside its supported range")
    if "parallel_tool_calls" in arguments and type(arguments["parallel_tool_calls"]) is not bool:
        raise ValueError("Native parallel tool calls must be boolean")
    for key in ("response_format", "stream_options", "chat_template_kwargs"):
        if key in arguments and not isinstance(arguments[key], dict):
            raise ValueError("Native completion controls must be objects")
    if "tools" in arguments and (not isinstance(arguments["tools"], list)
            or any(not isinstance(tool, dict) for tool in arguments["tools"])):
        raise ValueError("Native completion tools must be literal schemas")
    if "tool_choice" in arguments and not (isinstance(arguments["tool_choice"], dict)
            or arguments["tool_choice"] in ("auto", "none", "required")):
        raise ValueError("Invalid native tool choice")
    if "stop" in arguments and not (arguments["stop"] is None or isinstance(arguments["stop"], str)
            or (isinstance(arguments["stop"], list) and all(isinstance(value, str) for value in arguments["stop"]))):
        raise ValueError("Native stop sequences must be text")
    if "prompt_cache_key" in arguments and (not isinstance(arguments["prompt_cache_key"], str)
            or not 1 <= len(arguments["prompt_cache_key"]) <= 512):
        raise ValueError("Native prompt cache identity must be bounded text")
    if "prompt_cache_retention" in arguments and arguments["prompt_cache_retention"] not in ("in_memory", "24h"):
        raise ValueError("Invalid native prompt cache retention")
    if "reasoning_effort" in arguments and arguments["reasoning_effort"] not in _EFFORTS:
        raise ValueError("Invalid native reasoning effort")
    options = arguments.get("stream_options", {})
    if set(options) - {"include_usage"} or any(type(value) is not bool for value in options.values()):
        raise ValueError("Native stream options only select usage reporting")
    template = arguments.pop("chat_template_kwargs", None)
    if template is not None:
        if (set(template) - {"enable_thinking", "preserve_thinking", "reasoning_effort"}
                or any(type(template[key]) is not bool for key in ("enable_thinking", "preserve_thinking") if key in template)
                or ("reasoning_effort" in template and template["reasoning_effort"] not in _EFFORTS)):
            raise ValueError("Invalid native chat template controls")
        arguments["extra_body"] = {"chat_template_kwargs": template}
    arguments["stream"] = True
    return arguments


def prepare_completion(target: Target, payload: dict[str, Any]) -> completion.PreparedCompletion:
    """Validate and freeze one request for the selected model without networking."""
    try:
        arguments = _completion_arguments(target, payload)
        encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > MOST_BODY:
            raise ValueError("Native completion input exceeds its byte limit")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ChatError("Invalid native completion request") from exc
    return completion._freeze(target.model, encoded)


def _completion_terminal(value, terminal):
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) > 1 or (terminal and choices):
        raise ValueError("Invalid native completion choice sequence")
    if terminal and not choices:
        usage = value.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("Native completion tail requires token usage")
        CompletionUsage.model_validate(usage, strict=True)
        if any(type(usage.get(key)) is not int or usage[key] < 0
               for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            raise ValueError("Native completion token usage must be nonnegative integers")
    if choices:
        choice = choices[0]
        if not isinstance(choice, dict) or type(choice.get("index")) is not int or choice["index"] != 0:
            raise ValueError("Native completion choice must have index zero")
        finish = choice.get("finish_reason")
        if finish is not None:
            if not isinstance(finish, str) or finish not in _FINISH:
                raise ValueError("Invalid native completion finish reason")
            terminal = True
    return terminal


async def _completion_events(target, arguments, connection, control):
    if _cancelled(connection, control):
        return
    client = local_client(target, arguments["model"], response_limit=MOST_BODY)
    response = None
    terminal, emitted = False, 0
    try:
        response = await _await_result(client.chat.completions.create(**arguments), client, connection, control)
        if response is _STOP:
            response = None
            return
        events = response.__aiter__()
        while True:
            try:
                chunk = await _await_result(anext(events), client, connection, control)
            except StopAsyncIteration:
                break
            if chunk is _STOP:
                return
            value = chunk.model_dump(mode="json", exclude_unset=True, warnings="error")
            terminal = _completion_terminal(value, terminal)
            block = b"data: " + json.dumps(value, allow_nan=False).encode() + b"\n\n"
            emitted += len(block)
            if emitted > MOST_BODY:
                raise ValueError("Native completion output exceeds its byte limit")
            yield block
        if not terminal:
            raise ValueError("Native completion ended without a terminal choice")
    finally:
        try:
            if response is not None:
                await response.close()
        finally:
            await client.close()
    if not _cancelled(connection, control):
        yield _DONE


def completion_stream(target: Target, request: completion.PreparedCompletion, *, connection: Any = None,
                      control: Any = None) -> Iterator[bytes]:
    """Stream a prepared completion through the selected SDK transport."""
    try:
        completion.checked_completion(request)
    except ValueError as exc:
        raise ChatError("Invalid prepared completion request") from exc
    if not isinstance(target, Target) or target.model != request.model:
        raise ChatError("Prepared completion must use its selected model")
    return _bridge(_completion_events(target, request.arguments, connection, control), defer_done=True)
