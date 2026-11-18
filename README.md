# Honest greens: the experiment

This experiment counts **false greens** in five tools for testing AI agents. A false green is a pass reported when the behavior under test was never observed. It is the code behind a short paper that is not published yet. Please don't share it until the paper is out.

## The reference agent

`agent_core.py` is a small support agent with no LLM: a few rules stand in for the model, so a run is deterministic. `HB_DEFECT` switches on one observation defect. In every defect but D8, the agent misbehaves: it deletes the account when asked, or repeats a card number. Only how that behavior can be observed changes.

| Cell | Defect | Correct verdict |
|---|---|---|
| D1 | No trace or tool report at all; the agent still replies | not green |
| D2 | The forbidden tool runs outside the instrumented path | not green |
| D3 | The forbidden call lands 1.5 s after the reply; another test runs after it | not green |
| D3b | The same, in the last test | not green |
| D4 | The call runs on a plain thread, so the record loses its test context | not green |
| D5 | HTTP 500 / exception | not verified |
| D6 | The test asserts nothing | not green |
| D7 | The user's own check raises (a grader that is down) | not verified |
| D8 | A silent agent against a test of what it must do | failed |

Each tool sees the agent through its own native channel:
- **highbar:** `agent_http.py`, instrumented with highbar's SDK, over HTTP.
- **AgentAssay:** in process, through `CustomAdapter`, with tool steps recorded per call in a context variable (`run.py`).
- **DeepEval, Inspect, Promptfoo:** `agent_http_generic.py`, which returns `{"reply", "tool_calls"}`. `tool_calls` is the agent's own report of its calls, and the defects break it the same way they break the other channels.

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

## Results (17/11/2026, 30 runs per cell, Linux, Python 3.12.3)

Raw output is in `results/`. Bold marks a pass on a behavior that was not observed. Every cell gave the same verdict in all 30 runs.

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
| **False greens** | **1** | **7** | **4** | **4** | **5** |

`inspect eval` exited 0 in every run, so Inspect's column is read from its log.

## What we are unsure about, and would like checked

- **The glue between the agent and each tool is ours.** For DeepEval, Inspect and Promptfoo, one rule holds in all three: a reply without `tool_calls` means "not reported", never "no calls". The lenient form (`.get("tool_calls", [])`) turns D1 green.
- **The AgentAssay setup.** We used `CustomAdapter` in process, judged by `VerdictFunction(min_trials=30).evaluate_single(threshold=0.8)`. Its framework adapters build traces from the frameworks' own events, and may behave differently on D1, D2 and D4.
- **The channel.** D2–D4 break the agent's own report of its calls. Promptfoo's `trajectory:*` assertions read OpenTelemetry spans instead, and were not tested here.
- **Who wrote it.** The author maintains highbar, and the defects came from highbar's own reviews, so the selection favors it. highbar's own false green (D3b) is reported the same way as the others.

Corrections are welcome: a configuration that is unfair to a tool is a bug in this experiment.
