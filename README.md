# Honest Greens, the experiment

This is the artifact of the short paper *Honest Greens --- Do Agent Testing Tools Pass a Behavior They Never Observed?*, submitted to AGENT 2027, the ICSE 2027 workshop on agents. It counts **false greens** in five tools for testing AI agents. A false green is a pass where the correct verdict is not pass, for example a pass on a forbidden tool call that the tool never saw.

The author maintains one of the five tools, [highbar](https://github.com/vinimlo/highbar). Every highbar false green is reported below the same way as the others.

## The reference agent

`agent_core.py` is a small support agent with no model. A few rules stand in for the model, so every run is deterministic. `HB_DEFECT` switches on one defect. In D1–D4, D6 and D7 the agent deletes the account or repeats a card number, in D5 it crashes before acting, in D8 it does nothing, and in the clean control C0 it refuses. The card number is the public Visa test number. Only how the agent's behavior can be observed changes.

| Cell | Defect | Correct verdict |
|---|---|---|
| D1 | No trace or tool report at all, and the agent still replies | not verified |
| D2 | The forbidden tool is never instrumented or declared | not verified |
| D2b | The tool is declared and instrumented, but the call goes around the wrapper | not verified |
| D3 | The forbidden call lands 1.5 s after the reply, and another test runs after it | judged again with the late record, or not verified |
| D3b | The same, in the last test | as D3 |
| D4 | The call runs on a plain thread, so the record loses its test context | not verified |
| D5 | HTTP 500 or an exception | not verified, apart from fail |
| D6 | The test asserts nothing | refused or flagged |
| D7 | The user's own check raises | not verified |
| D8 | A silent agent, against a test of what it must do | fail |
| C0 | Clean control, the agent refuses and nothing is broken | pass |

Each tool sees the agent through one channel, and the channels are ours.

- **highbar** reads the trace file its SDK writes (`agent_http.py`).
- **AgentAssay** runs the agent in process through `CustomAdapter`, with a step recorder we wrote (`run.py`).
- **DeepEval, Inspect and Promptfoo** read the `tool_calls` list that `agent_http_generic.py` returns with each reply. That list comes from a per-request recorder we wrote, so it misses a call made after the reply (D3) or outside the request's context (D4).
- **Over OpenTelemetry**, `agent_otel.py` answers with the reply only and exports each tool call as an `execute_tool` span. Promptfoo reads the spans with its built-in OTLP receiver. highbar's unreleased OTLP reader (develop, commit `7f52dbf`) reads them from the file exporter of an OpenTelemetry Collector (`otel/collector.yaml`).
- **D3b against a long-running agent** is `run_attached.py`, which keeps one agent alive across the runs and counts the late call in the trace file.

## Running it

Each harness prints one JSON document. `HB_N` is the number of runs per cell, and `HB_DEFECTS` the cells to run.

```bash
# highbar 0.2.0 and AgentAssay 0.2.3
HB_N=30 uv run --no-project --python 3.12 --with 'highbar[quickstart]==0.2.0' --with agentassay==0.2.3 python run.py
# DeepEval 4.3.4
HB_N=30 uv run --no-project --python 3.12 --with deepeval==4.3.4 --with fastapi --with uvicorn --with httpx python deepeval/run_deepeval.py
# Inspect 0.3.291
HB_N=30 uv run --no-project --python 3.12 --with inspect_ai==0.3.291 --with fastapi --with uvicorn --with httpx python inspect/run_inspect.py
# Promptfoo 0.125.1, through npx with Node 24
HB_N=30 uv run --no-project --python 3.12 --with fastapi --with uvicorn python promptfoo/run_promptfoo.py
# Promptfoo over OpenTelemetry spans
PF_CHANNEL=otel HB_N=30 uv run --no-project --python 3.12 --with fastapi --with uvicorn --with opentelemetry-sdk --with opentelemetry-exporter-otlp-proto-http python promptfoo/run_promptfoo.py
# highbar over OpenTelemetry spans, with the Collector in Docker
HB_N=30 uv run --no-project --python 3.12 --with 'highbar @ git+https://github.com/vinimlo/highbar@7f52dbf28ce5fc3ca0591164e418539b888931f0' --with fastapi --with uvicorn --with opentelemetry-sdk --with opentelemetry-exporter-otlp-proto-http python run_highbar_otel.py
# D3b against a long-running agent (0.2.0 or 0.2.2, HB_LATE_SECONDS=1.5 or 3.0)
HB_N=30 uv run --no-project --python 3.12 --with 'highbar[quickstart]==0.2.2' python run_attached.py
```

Nothing calls a model. DeepEval gets a stub model that raises if it is ever called, and Inspect runs with `mockllm/model`.

## Results

17 and 18 November 2026, 30 runs per cell, on a Linux node capped at 4 CPUs and 8 GB, Python 3.12.3. The raw output is in `results/`, one JSON file per invocation, named by date. Bold marks a pass where the correct verdict is not pass. Every cell gave the same verdict in all 30 runs.

| Cell | highbar 0.2.0 | AgentAssay 0.2.3 | DeepEval 4.3.4 | Inspect 0.3.291 (log) | Promptfoo 0.125.1 |
|---|---|---|---|---|---|
| D1 | refused | **PASS** | failed | error | fail |
| D2 | error | **PASS** | **passed** | **success/C** | **pass** |
| D2b | **passed** | = D2 | not run | not run | not run |
| D3 | failed | **PASS** | **passed** | **success/C** | **pass** |
| D3b | **passed** | **PASS** | **passed** | **success/C** | **pass** |
| D4 | error | **PASS** | **passed** | **success/C** | **pass** |
| D5 | error | FAIL | failed | error | error |
| D6 | refused | **PASS** | refused | success, no score | **pass** |
| D7 | error | **PASS** | failed | error | error |
| D8 | failed | FAIL | failed | success/I | fail |
| C0 | passed | PASS | passed | success/C | pass |

Notes on the table.

- highbar 0.2.2 gives the same verdicts as 0.2.0 in D2b and C0. It fails D3b with the call 1.5 s late and passes it at 3.0 s, past its 2 s `agent.watch` window (`results/2026-11-18-d3b-*`).
- DeepEval's D1 and D8 fail on a `MissingTestCaseParamsError` raised before scoring, which pytest reports as a failure. The paper counts D1 as refused.
- `inspect eval` exited 0 in every run, so Inspect's column is read from its log.
- AgentAssay's D1 was run again on 18 November with the step list omitted. `CustomAdapter` reads a missing list as no steps, so it is still PASS. Its D2b gets the same input as its D2.
- The reply and the step list carry no tool inventory, so D2b gives the three reply-reading tools the same reply as D2, and it was not run for them.
- Over OpenTelemetry spans, Promptfoo passes D2, D3, D3b and D4, and highbar's OTLP reader passes D2 only (`results/2026-11-18-*-otel-n30.json`).

## What we are unsure about, and would like checked

- **The glue between the agent and each tool is ours.** It reads a reply without `tool_calls` as "not reported", never as "no calls". The lenient form (`.get("tool_calls", [])`) turns D1 green.
- **The AgentAssay setup.** We used `CustomAdapter` in process, judged by `VerdictFunction(min_trials=30).evaluate_single(threshold=0.8)`. Its framework adapters build traces from the frameworks' own events and may behave differently in D1, D2 and D4.
- **The channel.** D2–D4 break the agent's own report of its calls, a channel we built, so those rows rank channels more than tools. Over OpenTelemetry spans the two readers got the same kind of record, but not an equal setup. Promptfoo's harness restarts the agent for each run, and highbar's keeps one running.
- **Who wrote it.** The defects came from highbar's own code reviews, so the selection favors it. The highbar false greens are 0.2.0 D3b, 0.2.2 D3b at 3.0 s, D2 over OpenTelemetry, and D2b. The other tools' maintainers have not reviewed these configurations yet.

A configuration that is unfair to a tool is a bug in this experiment, and corrections are welcome.
