"""Honest greens, D0-D4 over OpenTelemetry, read by highbar's OTLP reader.

The setup of highbar's OpenTelemetry guide (develop). agent_otel.py exports its spans over
OTLP/HTTP to an OpenTelemetry Collector, whose `file` exporter writes them to
otel/out/traces.jsonl, and highbar reads that file in attach mode (`format: otlp`, with
the agent's tools listed). The agent is started once per cell and keeps running across
its runs, as a deployed service does. One run is one `highbar run` of the cell's spec.
After each run the harness waits HB_LATE_SECONDS + 1 s, so a late call lands before the
next run, and counts in the file whether the forbidden call was made.

The Collector runs in Docker (COLLECTOR_IMAGE) with the host network and a CPU and memory
limit, removed on every way out. Prints one JSON document to stdout. Nothing calls a model.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
N = int(os.environ.get("HB_N", "30"))
LATE = float(os.environ.get("HB_LATE_SECONDS", "1.5"))
DEFECTS = os.environ.get("HB_DEFECTS", "D0 D2 D3 D3b D4").split()
COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.120.0"
PORT = 8744
OUT = HERE / "otel" / "out"
TRACES = OUT / "traces.jsonl"
GUARD = "never deletes an account from chat"
CONTROL = "looks the order up"
CONTAINER = f"hb-otelcol-{os.getpid()}"


def spec_for(defect: str) -> str:
    return "specs/otel_guardrail_last.highbar.yaml" if defect == "D3b" else "specs/otel_guardrail.highbar.yaml"


def wait_port(port: int, seconds: float = 30) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"nothing listens on {port}")


def start_collector() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    TRACES.unlink(missing_ok=True)
    TRACES.touch()
    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER, "--network", "host", "--cpus", "1",
         "--memory", "512m", "--user", f"{os.getuid()}:{os.getgid()}",
         "-v", f"{HERE / 'otel'}:/etc/hbotel:ro", "-v", f"{OUT}:/var/otel",
         COLLECTOR_IMAGE, "--config", "/etc/hbotel/collector.yaml"],
        check=True, capture_output=True,
    )
    wait_port(4318)


def stop_collector() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


def start_agent(defect: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "HB_DEFECT": defect[:2], "HB_PORT": str(PORT), "HB_LATE_SECONDS": str(LATE),
           "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
    proc = subprocess.Popen([sys.executable, "agent_otel.py"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1):
                return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("agent did not come up")


def stop_agent(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def deletes_in_file() -> int:
    return TRACES.read_text().count('"execute_tool delete_account"') if TRACES.exists() else 0


def status_of(data: dict[str, Any], name: str) -> tuple[str, str]:
    for s in data.get("specs", []):
        for t in s.get("tests", []):
            if t.get("name") == name:
                return t.get("status", "unknown"), json.dumps(t.get("errors"))[:600]
    return "unknown", f"no test {name!r}"


def run_cell(defect: str) -> dict[str, Any]:
    exe = shutil.which("highbar") or "highbar"
    verdicts: Counter[str] = Counter()
    control: Counter[str] = Counter()
    exits: Counter[int] = Counter()
    made: Counter[str] = Counter()
    first: dict[str, str] = {}
    control_first: dict[str, str] = {}
    agent = start_agent(defect)
    try:
        for _ in range(N):
            before = deletes_in_file()
            with tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp) / "r.json"
                proc = subprocess.run([exe, "run", spec_for(defect), "--report", f"json={report}"],
                                      cwd=HERE, capture_output=True, text=True, timeout=120)
                exits[proc.returncode] += 1
                tail = (proc.stdout + proc.stderr).strip()[-600:]
                if report.exists():
                    data = json.loads(report.read_text())
                    label, why = status_of(data, GUARD)
                    c_label, c_why = status_of(data, CONTROL)
                else:
                    label, why = ("refused" if proc.returncode == 2 else "unknown"), tail
                    c_label, c_why = label, tail
                verdicts[label] += 1
                first.setdefault(label, why)
                control[c_label] += 1
                control_first.setdefault(c_label, c_why)
            time.sleep(LATE + 1.0)
            made["made" if deletes_in_file() > before else "not made"] += 1
    finally:
        stop_agent(agent)
    return {"verdicts": dict(verdicts), "exit_codes": {str(k): v for k, v in exits.items()},
            "late_or_any_delete_span": dict(made), "first": first,
            "control": {"verdicts": dict(control), "first": control_first}}


def on_signal(signum: int, _frame: Any) -> None:
    raise SystemExit(128 + signum)


def main() -> None:
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    out: dict[str, Any] = {"tool": "highbar", "channel": "otel", "highbar": version("highbar"),
                           "collector": COLLECTOR_IMAGE, "n": N, "late_seconds": LATE, "cells": {}}
    started = time.time()
    start_collector()
    try:
        for defect in DEFECTS:
            t0 = time.time()
            cell = run_cell(defect)
            cell["seconds"] = round(time.time() - t0, 1)
            out["cells"][defect] = cell
            print(f"# {defect} done in {cell['seconds']}s: {cell['verdicts']}", file=sys.stderr, flush=True)
    finally:
        stop_collector()
    out["seconds"] = round(time.time() - started, 1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
