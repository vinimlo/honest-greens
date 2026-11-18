"""The reference agent over HTTP for tools that read tool calls from the reply.

POST /chat {"message": ...} -> {"reply": ..., "tool_calls": [{"name", "args", "result"}]}

`tool_calls` is the agent's own instrumentation, recorded per request through a
context variable, the way an app exposes its trajectory to an external eval tool.
The defects break it as they break every other channel:
D1 no recorder at all (no `tool_calls` key), D2 delete_account not instrumented,
D3 the call lands after the reply, D4 the call runs on a thread without the
context. D5 answers HTTP 500.
"""

from __future__ import annotations

import contextvars
import os
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import agent_core
from agent_core import AgentDown, Tools, answer

PORT = int(os.environ.get("HB_PORT", "8742"))
DEFECT = agent_core.DEFECT

_calls: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("calls", default=None)


def instrument(name: str, fn: Any) -> Any:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        out = fn(*args, **kwargs)
        calls = _calls.get()
        if calls is not None:  # no list in this context: the record is lost
            calls.append({"name": name, "args": dict(zip(("order_id",), args)) | kwargs, "result": out})
        return out

    return wrapped


get_order = instrument("get_order", agent_core.raw_get_order)
delete_account = instrument("delete_account", agent_core.raw_delete_account)
if DEFECT == "D1":
    tools = Tools(agent_core.raw_get_order, agent_core.raw_delete_account)
elif DEFECT == "D2":
    tools = Tools(get_order, agent_core.raw_delete_account)
else:
    tools = Tools(get_order, delete_account)

app = FastAPI()


class Message(BaseModel):
    message: str


@app.post("/chat")
def chat(body: Message) -> object:
    calls: list[dict[str, Any]] = []
    token = _calls.set(calls)
    try:
        reply = answer(body.message, tools)
    except AgentDown as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        _calls.reset(token)
    if DEFECT == "D1":
        return {"reply": reply}
    return {"reply": reply, "tool_calls": list(calls)}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
