"""Honest greens, Inspect harness: 9 cells x HB_N runs, one `inspect eval` per run.

For each defect: start agent_http_generic.py (HB_DEFECT, HB_PORT=8745), run
`inspect eval inspect/tasks.py@<task> --model mockllm/model` HB_N times at default
settings, the way a CI job would, read the verdict of the sample under study from
Inspect's own log (read_eval_log), stop the agent.

A verdict label is "<log.status>/<the sample's score value>", or
"<log.status>/sample error" when the sample has an error, "<log.status>/no score"
when it has neither, and "<log.status>/sample not in log" when the log has no such
sample. Prints one JSON document to stdout. Nothing calls a model.
"""

from __future__ import annotations

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

import httpx

from inspect_ai.log import read_eval_log

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # experimento/
PORT = 8745
N = int(os.environ.get("HB_N", "30"))
DEFECTS = os.environ.get("HB_DEFECTS", "D1 D2 D3 D3b D4 D5 D6 D7 D8").split()

# defect -> (task in tasks.py, id of the sample under study)
CELLS = {
    "D1": ("guardrail", "delete"),
    "D2": ("guardrail", "delete"),
    "D3": ("guardrail", "delete"),
    "D3b": ("guardrail_last", "delete"),
    "D4": ("guardrail", "delete"),
    "D5": ("guardrail", "delete"),
    "D6": ("empty", "delete"),
    "D7": ("check", "card"),
    "D8": ("positive", "order"),
}


def start_agent(defect: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "HB_DEFECT": defect[:2], "HB_PORT": str(PORT)}
    proc = subprocess.Popen(
        [sys.executable, "agent_http_generic.py"], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError(f"agent for {defect} did not come up on port {PORT}")


def stop_agent(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def inspect_cmd() -> list[str]:
    exe = shutil.which("inspect")
    return [exe] if exe else [sys.executable, "-m", "inspect_ai"]


def one_run(task: str, sample_id: str) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "HB_PORT": str(PORT)}
        proc = subprocess.run(
            [*inspect_cmd(), "eval", f"inspect/tasks.py@{task}", "--model", "mockllm/model",
             "--log-dir", tmp],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
        )
        tail = (proc.stdout + proc.stderr).strip()[-600:]
        logs = sorted(Path(tmp).glob("*.eval")) + sorted(Path(tmp).glob("*.json"))
        if not logs:
            return proc.returncode, "no log", tail
        log = read_eval_log(str(logs[-1]))
        sample = next((s for s in (log.samples or []) if s.id == sample_id), None)
        if sample is None:
            why = log.error.message if log.error else tail
            return proc.returncode, f"{log.status}/sample not in log", why[:600]
        if sample.error is not None:
            return proc.returncode, f"{log.status}/sample error", sample.error.message[:600]
        if not sample.scores:
            return proc.returncode, f"{log.status}/no score", (
                f"results={log.results.model_dump_json() if log.results else None}; output: {tail}"
            )[:600]
        name, score = next(iter(sample.scores.items()))
        return proc.returncode, f"{log.status}/{score.value}", f"{name}: {score.explanation}"[:600]


def run_cell(defect: str) -> dict[str, Any]:
    task, sample_id = CELLS[defect]
    verdicts: Counter[str] = Counter()
    exits: Counter[int] = Counter()
    first: dict[str, str] = {}
    agent = start_agent(defect)
    try:
        for _ in range(N):
            code, label, why = one_run(task, sample_id)
            exits[code] += 1
            verdicts[label] += 1
            first.setdefault(label, why)
    finally:
        if defect.startswith("D3"):
            time.sleep(2.0)  # let the late calls land before the agent goes
        stop_agent(agent)
        time.sleep(0.3)  # let the port go
    return {"verdicts": dict(verdicts), "exit_codes": {str(k): v for k, v in exits.items()}, "first": first}


def main() -> None:
    out: dict[str, Any] = {
        "tool": "inspect",
        "version": version("inspect_ai"),
        "n": N,
        "python": platform.python_version(),
        "model": "mockllm/model",
        "cells": {},
    }
    started = time.time()
    for defect in DEFECTS:
        t0 = time.time()
        out["cells"][defect] = run_cell(defect)
        out["cells"][defect]["seconds"] = round(time.time() - t0, 1)
        print(f"# {defect} done in {out['cells'][defect]['seconds']}s", file=sys.stderr, flush=True)
    out["seconds"] = round(time.time() - started, 1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
