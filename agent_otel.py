"""The reference agent over HTTP, instrumented with OpenTelemetry instead of its reply.

POST /chat {"message": ...} -> {"reply": ...}

The reply carries no tool calls. The trajectory goes out as spans, the way an app
instrumented with OpenTelemetry exposes it to an eval tool that reads traces:
- the request continues the caller's trace from the W3C `traceparent` header, under a
  server span `invoke_agent`;
- each instrumented tool call is a child span `execute_tool <name>` with the GenAI
  attributes (`gen_ai.operation.name`, `gen_ai.tool.name`) and the generic ones
  Promptfoo's trajectory assertions read (`tool.name`, `tool.arguments`).

Spans are exported over OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT (default
http://127.0.0.1:4318) with a SimpleSpanProcessor, so each span leaves as soon as it
ends. That favors the tool under test: a BatchSpanProcessor would hold spans for up to
5 s.

The defects break the instrumentation as they break every other channel (agent_core):
D2 delete_account is not instrumented, D3 the call lands 1.5 s after the reply on a
thread that keeps the context (same trace, late span), D4 the call runs on a plain
thread that loses the context (the span starts a new trace). D0 is no defect: the
instrumented delete_account runs inside the request, the control that shows the
channel catches the forbidden call.
"""

from __future__ import annotations

import json
import os
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import propagate
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import SpanKind

import agent_core
from agent_core import AgentDown, Tools, answer

PORT = int(os.environ.get("HB_PORT", "8744"))
DEFECT = agent_core.DEFECT
ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318").rstrip("/")

provider = TracerProvider(resource=Resource.create({"service.name": "reference-agent"}))
provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{ENDPOINT}/v1/traces")))
# Not the global provider: FastAPI 0.164 traces every request by itself when a global SDK
# provider is set, /health included, and those spans would go out too. Only the agent's own
# spans leave, as from an app instrumented by hand.
tracer = provider.get_tracer("reference-agent")


def instrument(name: str, fn: Any) -> Any:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        arguments = dict(zip(("order_id",), args)) | kwargs
        with tracer.start_as_current_span(f"execute_tool {name}", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("tool.name", name)
            span.set_attribute("tool.arguments", json.dumps(arguments))
            # the GenAI conventions' name, which highbar's OTLP reader takes the arguments from
            span.set_attribute("gen_ai.tool.call.arguments", json.dumps(arguments))
            return fn(*args, **kwargs)

    return wrapped


get_order = instrument("get_order", agent_core.raw_get_order)
delete_account = instrument("delete_account", agent_core.raw_delete_account)
if DEFECT == "D2":
    tools = Tools(get_order, agent_core.raw_delete_account)
else:
    tools = Tools(get_order, delete_account)

app = FastAPI()


@app.post("/chat")
async def chat(request: Request) -> object:
    body = await request.json()
    parent = propagate.extract(dict(request.headers))
    with tracer.start_as_current_span("invoke_agent", context=parent, kind=SpanKind.SERVER) as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        received = request.headers.get("traceparent", "")
        span.set_attribute("hb.traceparent_received", received)
        if log := os.environ.get("HB_TRACEPARENT_LOG"):  # for the probe: was the header sent?
            with open(log, "a") as f:
                f.write(f"{body['message']!r} traceparent={received!r}\n")
        try:
            reply = answer(body["message"], tools)
        except AgentDown as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    return {"reply": reply}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
