"""The reference agent as an HTTP service, instrumented with highbar's SDK.

D1 writes no trace at all: the SDK is never created.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import agent_core
from agent_core import AgentDown, Tools, answer

PORT = int(os.environ.get("HB_PORT", "8741"))

app = FastAPI()

if agent_core.DEFECT == "D1":
    tools = Tools(agent_core.raw_get_order, agent_core.raw_delete_account)
else:
    import highbar

    hb = highbar.Highbar()
    app.add_middleware(highbar.asgi.Middleware, hb=hb)

    @hb.tool
    def get_order(order_id: str) -> dict[str, str]:
        return agent_core.raw_get_order(order_id)

    if agent_core.DEFECT == "D2":
        # the agent declares only what it instruments
        tools = Tools(get_order, agent_core.raw_delete_account)
    else:

        @hb.tool
        def delete_account() -> dict[str, str]:
            return agent_core.raw_delete_account()

        tools = Tools(get_order, delete_account)


class Message(BaseModel):
    message: str


@app.post("/chat")
def chat(body: Message) -> object:
    try:
        return {"reply": answer(body.message, tools)}
    except AgentDown as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
