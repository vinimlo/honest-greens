"""The user's own glue between DeepEval and the external HTTP agent.

DeepEval does not call the app under test: the user calls it and builds an
`LLMTestCase` from what came back (https://deepeval.com/docs/evaluation-test-cases).
The agent reports its tool calls in `tool_calls`; each becomes a `ToolCall`.

Mapping choice, kept as literal as possible: when the reply has no `tool_calls`
key at all, `tools_called` is left unset (None, DeepEval's default for "not
provided"), never `[]` (which DeepEval reads as "no tool was called").
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from deepeval.test_case import LLMTestCase, ToolCall

AGENT_URL = os.environ.get("HB_AGENT_URL", "http://127.0.0.1:8744/chat")


def ask(message: str, **fields: Any) -> LLMTestCase:
    response = httpx.post(AGENT_URL, json={"message": message}, timeout=30)
    response.raise_for_status()
    data = response.json()
    calls = data.get("tool_calls")
    tools_called = (
        None
        if calls is None
        else [
            ToolCall(name=c["name"], input_parameters=c.get("args"), output=c.get("result"))
            for c in calls
        ]
    )
    return LLMTestCase(
        input=message,
        actual_output=data["reply"],
        tools_called=tools_called,
        **fields,
    )
