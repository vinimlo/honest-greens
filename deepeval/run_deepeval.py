"""Honest greens, DeepEval harness: the defects x HB_N runs of `deepeval test run`.

One run is what a CI job gets from one invocation: a fresh working folder, the
reference agent started with the defect, `deepeval test run <cell's file>`, the
agent stopped. The verdict of the test under study comes from pytest's JUnit XML
(DeepEval runs on pytest and passes extra flags through): passed / failed /
error / skipped. D6 is "refused" when DeepEval rejects the metric-less test case.
DeepEval's own per-metric record (DEEPEVAL_RESULTS_FOLDER) is kept as context.

No paid key: every *_API_KEY is removed from the environment, telemetry is off.
Prints one JSON document to stdout.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parent
N = int(os.environ.get("HB_N", "30"))
DEFECTS = os.environ.get("HB_DEFECTS", "D1 D2 D3 D3b D4 D5 D6 D7 D8").split()
PORT = 8744

GUARD = "test_never_deletes_an_account_from_chat"
CELLS = {  # defect: (test file, test under study)
    "D1": ("test_guardrail.py", GUARD),
    "D2": ("test_guardrail.py", GUARD),
    "D3": ("test_guardrail.py", GUARD),
    "D3b": ("test_guardrail_last.py", GUARD),
    "D4": ("test_guardrail.py", GUARD),
    "D5": ("test_guardrail.py", GUARD),
    "D6": ("test_empty.py", GUARD),
    "D7": ("test_check.py", "test_never_repeats_a_card_number"),
    "D8": ("test_positive.py", "test_looks_the_order_up"),
}
REFUSAL = "missing expectations"  # DeepEval's ValueError for a test case with nothing to evaluate


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.update(
        DEEPEVAL_TELEMETRY_OPT_OUT="1",
        DEEPEVAL_NO_INSPECT_PROMPT="1",
        DEEPEVAL_DISABLE_DOTENV="1",
        HB_AGENT_URL=f"http://127.0.0.1:{PORT}/chat",
    )
    return env


def start_agent(defect: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, "agent_http_generic.py"],
        cwd=EXPERIMENT,
        env={**env, "HB_DEFECT": defect[:2], "HB_PORT": str(PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    stop_agent(proc)
    raise RuntimeError(f"agent for {defect} did not become healthy")


def stop_agent(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def deepeval_exe() -> str:
    found = shutil.which("deepeval")
    if found:
        return found
    return str(Path(sys.executable).parent / "deepeval")


def junit_verdict(junit: Path, test_name: str) -> tuple[str, str] | None:
    if not junit.exists():
        return None
    for case in ET.parse(junit).getroot().iter("testcase"):
        if case.get("name") != test_name:
            continue
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                label = {"failure": "failed"}.get(tag, tag)
                return label, ((node.get("message") or "") + "\n" + (node.text or "")).strip()
        return "passed", ""
    return None


def deepeval_record(results: Path, test_name: str) -> list[dict[str, Any]]:
    """DeepEval's own per-metric record of the test, from the exported test run."""
    for path in sorted(results.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for case in data.get("testCases") or []:
            if case.get("name") == test_name:
                return [
                    {k: m.get(k) for k in ("name", "success", "score", "reason", "error")}
                    for m in case.get("metricsData") or []
                ]
    return []


def run_cell(defect: str) -> dict[str, Any]:
    test_file, test_name = CELLS[defect]
    env = clean_env()
    verdicts: Counter[str] = Counter()
    exits: Counter[int] = Counter()
    first: dict[str, str] = {}
    for _ in range(N):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            junit, results = work / "junit.xml", work / "results"
            run_env = {**env, "DEEPEVAL_RESULTS_FOLDER": str(results), "DEEPEVAL_HOME": str(work / "home")}
            agent = start_agent(defect, run_env)
            try:
                proc = subprocess.run(
                    [deepeval_exe(), "test", "run", str(HERE / test_file), f"--junitxml={junit}"],
                    cwd=work, env=run_env, capture_output=True, text=True, timeout=180,
                )
            finally:
                stop_agent(agent)
            exits[proc.returncode] += 1
            found = junit_verdict(junit, test_name)
            if found is None:
                label, why = "unknown", (proc.stdout + proc.stderr).strip()[-600:]
            else:
                label, why = found
                if defect == "D6" and label == "failed" and REFUSAL in why:
                    label = "refused"
                record = deepeval_record(results, test_name)
                if record:
                    why = (why + "\ndeepeval: " + json.dumps(record)).strip()
            verdicts[label] += 1
            first.setdefault(label, why[:800])
        time.sleep(0.2)  # let the port go
    if defect.startswith("D3"):
        time.sleep(2.0)
    return {"verdicts": dict(verdicts), "exit_codes": {str(k): v for k, v in exits.items()}, "first": first}


def main() -> None:
    out: dict[str, Any] = {
        "tool": "deepeval",
        "version": version("deepeval"),
        "pytest": version("pytest"),
        "python": platform.python_version(),
        "n": N,
        "cells": {},
    }
    started = time.time()
    for defect in DEFECTS:
        t0 = time.time()
        cell = run_cell(defect)
        cell["seconds"] = round(time.time() - t0, 1)
        out["cells"][defect] = cell
        print(f"# {defect} done in {cell['seconds']}s", file=sys.stderr, flush=True)
    out["seconds"] = round(time.time() - started, 1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
