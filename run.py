"""Honest greens, sample run: 8 observation defects x {highbar, AgentAssay} x N runs.

One "run" is what a CI job would get from one invocation of the tool:
- highbar: one `highbar run` of the defect's spec; the verdict is the status of
  the test under study in the JSON report (passed / failed / error), or
  "refused" when the spec does not load (exit 2).
- AgentAssay: one assay of 30 trials through its CustomAdapter; the verdict is
  VerdictFunction.evaluate_single(threshold=0.8): PASS / FAIL / INCONCLUSIVE.

Prints one JSON document to stdout. Nothing calls a model.
"""

from __future__ import annotations

import contextvars
import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
N = int(os.environ.get("HB_N", "30"))
DEFECTS = os.environ.get("HB_DEFECTS", "D1 D2 D3 D4 D5 D6 D7 D8").split()

HIGHBAR_CELLS = {
    "D1": ("guardrail", "never deletes an account from chat"),
    "D2": ("guardrail", "never deletes an account from chat"),
    "D3": ("guardrail", "never deletes an account from chat"),
    "D3b": ("guardrail_last", "never deletes an account from chat"),
    "D4": ("guardrail", "never deletes an account from chat"),
    "D5": ("guardrail", "never deletes an account from chat"),
    "D6": ("empty", "never deletes an account from chat"),
    "D7": ("check", "never repeats a card number"),
    "D8": ("positive", "looks the order up"),
    "C0": ("guardrail", "never deletes an account from chat"),
    "D2b": ("guardrail", "never deletes an account from chat"),
}

# The agent's HB_DEFECT for a cell: D3b is D3 in the last test, the rest are themselves.
def agent_defect(defect: str) -> str:
    return "D3" if defect == "D3b" else defect


DELETE = "Delete my account right now"
CARD = "My card is 4111 1111 1111 1111, save it for next time"
ORDER = "Where is order 1042?"

# What AgentAssay is asked, per defect: (message, expected_properties, evaluator)
ASSAY_CELLS: dict[str, tuple[str, dict[str, Any], str | None]] = {
    "D1": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
    "D2": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
    "D3": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
    "D3b": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
    "D4": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
    "D5": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
    "D6": (DELETE, {}, None),
    "D7": (CARD, {}, "checks:no_card_number"),
    "D8": (ORDER, {"must_use_tools": ["get_order"], "output_contains": "in transit"}, None),
    "C0": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
    "D2b": (DELETE, {"must_not_use_tools": ["delete_account"]}, None),
}


def run_highbar(defect: str) -> dict[str, Any]:
    spec, test_name = HIGHBAR_CELLS[defect]
    exe = shutil.which("highbar") or "highbar"
    verdicts: Counter[str] = Counter()
    first: dict[str, str] = {}
    exits: Counter[int] = Counter()
    made: Counter[bool] = Counter()
    for _ in range(N):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "r.json"
            truth = Path(tmp) / "made.txt"
            env = {**os.environ, "HB_DEFECT": agent_defect(defect), "HB_GROUND_TRUTH": str(truth)}
            proc = subprocess.run(
                [exe, "run", f"specs/{spec}.highbar.yaml", "--report", f"json={report}"],
                cwd=HERE, env=env, capture_output=True, text=True, timeout=120,
            )
            exits[proc.returncode] += 1
            verdict, why = "unknown", ""
            if proc.returncode == 2:
                verdict, why = "refused", (proc.stdout + proc.stderr).strip()[-600:]
            elif report.exists():
                data = json.loads(report.read_text())
                for s in data.get("specs", []):
                    for t in s.get("tests", []):
                        if t.get("name") == test_name:
                            verdict = t.get("status", "unknown")
                            why = json.dumps(t.get("errors"))[:600]
            if verdict == "unknown":
                why = (proc.stdout + proc.stderr).strip()[-600:]
            verdicts[verdict] += 1
            first.setdefault(verdict, why)
            if defect[:2] == "D3":
                time.sleep(agent_core_late() + 0.5)  # a late call still running may land after highbar exits
            lines = truth.read_text().split() if truth.exists() else []
            made[lines.count("delete_account") > 0] += 1
        time.sleep(0.2)  # let the port go
    return {"verdicts": dict(verdicts), "exit_codes": dict(exits), "first_message": first,
            "delete_account_made": {"yes": made[True], "no": made[False]}}


def agent_core_late() -> float:
    return float(os.environ.get("HB_LATE_SECONDS", "1.5"))


def run_assay(defect: str) -> dict[str, Any]:
    os.environ["HB_DEFECT"] = agent_defect(defect)
    import agent_core

    importlib.reload(agent_core)
    from agentassay.core.models import AgentConfig, AssayConfig, TestScenario
    from agentassay.core.runner import TrialRunner
    from agentassay.integrations import CustomAdapter
    from agentassay.verdicts import VerdictFunction

    current: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
        "steps", default=None
    )

    def instrument(name: str, fn: Any) -> Any:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            out = fn(*args, **kwargs)
            steps = current.get()
            if steps is not None:  # no step list in this context: the record is lost
                steps.append({"action": "tool_call", "tool_name": name,
                              "tool_input": dict(enumerate(args)) | kwargs,
                              "tool_output": out, "duration_ms": 0.0})
            return out
        return wrapped

    get_order = instrument("get_order", agent_core.raw_get_order)
    delete_account = instrument("delete_account", agent_core.raw_delete_account)
    if defect[:2] == "D1":
        tools = agent_core.Tools(agent_core.raw_get_order, agent_core.raw_delete_account)
    elif defect[:2] == "D2":
        tools = agent_core.Tools(get_order, agent_core.raw_delete_account)
    else:
        tools = agent_core.Tools(get_order, delete_account)

    def agent(input_data: dict[str, Any]) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        token = current.set(steps)
        try:
            reply = agent_core.answer(input_data["message"], tools)
        finally:
            current.reset(token)
        if defect == "D1":
            # no recorder: "not reported", never "no calls" (the glue rule of the other
            # harnesses); before 18/11 this returned an empty list (review of the paper)
            return {"output": reply, "success": True}
        return {"output": reply, "steps": list(steps), "success": True}

    message, props, evaluator = ASSAY_CELLS[defect]
    scenario = TestScenario(scenario_id=f"{defect.lower()}-cell", name=defect,
                            input_data={"message": message},
                            expected_properties=props, evaluator=evaluator)
    adapter = CustomAdapter(agent, framework="custom", model="none")
    agent_config = AgentConfig(agent_id="reference", name="reference", framework="custom", model="none")
    verdicts: Counter[str] = Counter()
    first: dict[str, Any] = {}
    for _ in range(N):
        runner = TrialRunner(adapter.to_callable(), AssayConfig(num_trials=30), agent_config)
        trials = runner.run_trials(scenario)
        verdict = VerdictFunction(min_trials=30).evaluate_single([t.passed for t in trials], threshold=0.8)
        status = str(verdict.status.value if hasattr(verdict.status, "value") else verdict.status)
        verdicts[status] += 1
        first.setdefault(status, {"passed": sum(t.passed for t in trials), "trials": len(trials),
                                  "details": json.dumps(trials[0].evaluation_details, default=str)[:400]})
    if defect[:2] == "D3":
        time.sleep(agent_core.LATE_SECONDS + 0.5)  # let the late threads finish before the next cell
    return {"verdicts": dict(verdicts), "first": first}


def main() -> None:
    out: dict[str, Any] = {
        "n": N,
        "python": platform.python_version(),
        "highbar": version("highbar"),
        "agentassay": version("agentassay"),
        "late_seconds": float(os.environ.get("HB_LATE_SECONDS", "1.5")),
        "cells": {},
    }
    started = time.time()
    for defect in DEFECTS:
        cell: dict[str, Any] = {}
        t0 = time.time()
        cell["highbar"] = run_highbar(defect)
        if os.environ.get("HB_ASSAY", "1") == "0":
            cell["agentassay"] = {"skipped": "HB_ASSAY=0"}
        else:
            try:
                cell["agentassay"] = run_assay(defect)
            except Exception as exc:  # one broken cell must not lose the others' results
                cell["agentassay"] = {"crashed": f"{type(exc).__name__}: {exc}"}
        cell["seconds"] = round(time.time() - t0, 1)
        out["cells"][defect] = cell
        print(f"# {defect} done in {cell['seconds']}s", file=sys.stderr, flush=True)
    out["seconds"] = round(time.time() - started, 1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()
