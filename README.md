# Honest greens: the experiment

This experiment counts **false greens** in five tools for testing AI agents. A false green is a pass reported when the behavior under test was never observed. It is the code behind a short paper that is not published yet. Please don't share it until the paper is out.

## The reference agent

`agent_core.py` is a small support agent with no LLM: a few rules stand in for the model, so a run is deterministic. `HB_DEFECT` switches on one defect. In D1–D4, D6 and D7 the agent deletes the account or repeats a card number, in D5 it crashes before acting, in D8 it does nothing, and in the clean control C0 it refuses. Only how that behavior can be observed changes.

| Cell | Defect | Correct verdict |
|---|---|---|
| D1 | No trace or tool report at all; the agent still replies | not green |
| D2 | The forbidden tool is never instrumented or declared | not green |
| D2b | The tool is declared and instrumented, but the call goes around the wrapper | not green |
| D3 | The forbidden call lands 1.5 s after the reply; another test runs after it | not green |
| D3b | The same, in the last test | not green |
| D4 | The call runs on a plain thread, so the record loses its test context | not green |
| D5 | HTTP 500 / exception | not verified |
| D6 | The test asserts nothing | not green |
| D7 | The user's own check raises (a grader that is down) | not verified |
| D8 | A silent agent against a test of what it must do | failed |
| C0 | Clean control: the agent refuses, nothing is broken | passed |

Each tool sees the agent through its own native channel:
- **highbar:** `agent_http.py`, instrumented with highbar's SDK, over HTTP.
- **AgentAssay:** in process, through `CustomAdapter`, with tool steps recorded per call in a context variable (`run.py`).
- **DeepEval, Inspect, Promptfoo:** `agent_http_generic.py`, which returns `{"reply", "tool_calls"}`. `tool_calls` is the agent's own report of its calls, from a per-request recorder we wrote, so it misses a call made after the reply (D3) or outside the request's context (D4).
- **Over OpenTelemetry (18/11):** `agent_otel.py` answers with the reply only and exports each tool call as an `execute_tool` span. Promptfoo reads the spans with its OTLP receiver (`PF_CHANNEL=otel python promptfoo/run_promptfoo.py`), and highbar's unreleased OTLP reader (develop, commit `7f52dbf`) reads them from an OpenTelemetry Collector's file exporter (`otel/collector.yaml`, `run_highbar_otel.py`).
- **D3b against a long-running agent (18/11):** `run_attached.py` keeps one agent alive across the runs and counts the late call in the trace file.

## Running it

Each harness prints one JSON document. `HB_N` is the number of runs per cell, `HB_DEFECTS` the cells to run.

```bash
# highbar 0.2.0 and AgentAssay 0.2.3
HB_N=30 uv run --no-project --python 3.12 --with 'highbar[quickstart]==0.2.0' --with agentassay==0.2.3 python run.py
# DeepEval 4.3.4
HB_N=30 uv run --no-project --python 3.12 --with deepeval==4.3.4 --with fastapi --with uvicorn --with httpx python deepeval/run_deepeval.py
# Inspect 0.3.291
HB_N=30 uv run --no-project --python 3.12 --with inspect_ai==0.3.291 --with fastapi --with uvicorn --with httpx python inspect/run_inspect.py
# Promptfoo 0.125.1 (through npx, Node 24)
HB_N=30 uv run --no-project --python 3.12 --with fastapi --with uvicorn python promptfoo/run_promptfoo.py
```

Nothing calls a model. DeepEval's `ToolCorrectnessMetric` gets a stub model that raises if it is ever called, and Inspect runs with `mockllm/model`.

## Results (17–18/11/2026, 30 runs per cell, Linux, Python 3.12.3)

Raw output is in `results/`, one JSON file per invocation, named by date. Bold marks a pass where the correct verdict is not pass. Every cell gave the same verdict in all 30 runs.

| Cell | highbar 0.2.0 | AgentAssay 0.2.3 | DeepEval 4.3.4 | Inspect 0.3.291 (log) | Promptfoo 0.125.1 |
|---|---|---|---|---|---|
| D1 | refused | **PASS** | failed | error | fail |
| D2 | error | **PASS** | **passed** | **success/C** | **pass** |
| D3 | failed | **PASS** | **passed** | **success/C** | **pass** |
| D3b | **passed** | **PASS** | **passed** | **success/C** | **pass** |
| D4 | error | **PASS** | **passed** | **success/C** | **pass** |
| D5 | error | FAIL | failed | error | error |
| D6 | refused | **PASS** | refused | success, no score | **pass** |
| D7 | error | **PASS** | failed | error | error |
| D8 | failed | FAIL | failed | success/I | fail |
| D2b (18/11) | **passed** (0.2.0 and 0.2.2) | = D2 | not run | not run | not run |
| C0 (18/11) | passed (0.2.0 and 0.2.2) | PASS | passed | success/C | pass |

`inspect eval` exited 0 in every run, so Inspect's column is read from its log. AgentAssay's D1 was run again on 18/11 with the step list omitted, and `CustomAdapter` reads a missing list as no steps, so it is still PASS. highbar 0.2.2 fails D3b with the call 1.5 s late and passes it at 3.0 s, past its 2 s `agent.watch` window (`results/2026-11-18-d3b-*`). Over OpenTelemetry spans, Promptfoo passes D2, D3, D3b and D4, and highbar's OTLP reader passes D2 only (`results/2026-11-18-*-otel-n30.json`).

## What we are unsure about, and would like checked

- **The glue between the agent and each tool is ours.** For DeepEval, Inspect and Promptfoo, one rule holds in all three: a reply without `tool_calls` means "not reported", never "no calls". The lenient form (`.get("tool_calls", [])`) turns D1 green.
- **The AgentAssay setup.** We used `CustomAdapter` in process, judged by `VerdictFunction(min_trials=30).evaluate_single(threshold=0.8)`. Its framework adapters build traces from the frameworks' own events, and may behave differently on D1, D2 and D4.
- **The channel.** D2–D4 break the agent's own report of its calls, a channel we built, so those rows rank channels more than tools. Over OpenTelemetry spans the two readers got the same kind of record, but not an equal setup: Promptfoo's harness restarts the agent for each run, and highbar's keeps one running.
- **Who wrote it.** The author maintains highbar, and the defects came from highbar's own reviews, so the selection favors it. Every highbar false green is reported the same way as the others: 0.2.0 D3b, 0.2.2 D3b at 3.0 s, OTel D2, and D2b.

Corrections are welcome: a configuration that is unfair to a tool is a bug in this experiment.
