"""Honest greens, Promptfoo harness: the defects x N runs, one `promptfoo eval` per run.

One run is what a CI job gets from one invocation of the tool: the reference agent
(agent_http_generic.py, HB_DEFECT, port 8743) is started, `promptfoo eval` runs the
defect's config once, the verdict of the test under study is read from Promptfoo's own
JSON output (`-o`), and the agent is stopped.

Verdict labels are Promptfoo's own: a result row with `success: true` is "pass"; with
`failureReason` 1 (ASSERT) it is "fail"; with `failureReason` 2 (ERROR) it is "error".
"refused" means Promptfoo wrote no output file and exited non-zero (the config did not
load).

Promptfoo runs through `npx -y promptfoo@<PROMPTFOO_VERSION>` on the host when its node
satisfies the package's engines (>= 22.22.0); otherwise in a named Node container with a
CPU and memory limit, removed on every way out. Each run gets a fresh PROMPTFOO_CONFIG_DIR
(empty cache and database, as a fresh CI job) and `--no-cache`, so no reply is reused.
Nothing calls a model. Stdlib only. Prints one JSON document to stdout.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

PROMPTFOO_VERSION = "0.125.1"
NODE_IMAGE = "node:24-bookworm-slim"
MIN_NODE = (22, 22, 0)

PF_DIR = Path(__file__).resolve().parent
HERE = PF_DIR.parent  # experimento/
N = int(os.environ.get("HB_N", "30"))
# PF_CHANNEL=otel reads the trajectory from OpenTelemetry spans (agent_otel.py) with the
# trajectory:* assertions, instead of the agent's own record in its reply.
CHANNEL = os.environ.get("PF_CHANNEL", "reply")
OTEL = CHANNEL == "otel"
DEFECTS = os.environ.get(
    "HB_DEFECTS", "D0 D2 D3 D3b D4" if OTEL else "D1 D2 D3 D3b D4 D5 D6 D7 D8"
).split()
PORT = 8744 if OTEL else 8743
AGENT = "agent_otel.py" if OTEL else "agent_http_generic.py"
CONTROL = "looks the order up"  # in the otel configs, the test that shows the spans arrived

GUARD = "never deletes an account from chat"
# defect -> (config under promptfoo/, description of the test under study)
CELLS = {
    "D1": ("guardrail.yaml", GUARD),
    "D2": ("guardrail.yaml", GUARD),
    "D3": ("guardrail.yaml", GUARD),
    "D3b": ("guardrail_last.yaml", GUARD),
    "D4": ("guardrail.yaml", GUARD),
    "D5": ("guardrail.yaml", GUARD),
    "D6": ("empty.yaml", GUARD),
    "D7": ("check.yaml", "never repeats a card number"),
    "D8": ("positive.yaml", "looks the order up"),
    "C0": ("guardrail.yaml", GUARD),  # the clean control: the agent refuses
}
if OTEL:
    CELLS = {d: ("otel_guardrail_last.yaml" if d == "D3b" else "otel_guardrail.yaml", GUARD)
             for d in ("D0", "D2", "D3", "D3b", "D4")}

FAILURE_REASON = {0: "pass", 1: "fail", 2: "error"}  # Promptfoo's ResultFailureReason


def node_ok() -> bool:
    node = shutil.which("node")
    if not node or not shutil.which("npx"):
        return False
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", out.strip())
    return bool(m) and tuple(int(x) for x in m.groups()) >= MIN_NODE


USE_HOST = os.environ.get("PF_RUNTIME", "auto") == "host" or (
    os.environ.get("PF_RUNTIME", "auto") == "auto" and node_ok()
)
WORK = Path(tempfile.mkdtemp(prefix=".pf-work-", dir=PF_DIR))  # inside the tree: the container mounts it
CONTAINERS: set[str] = set()


def cleanup_containers() -> None:
    for name in list(CONTAINERS):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        CONTAINERS.discard(name)


def cleanup() -> None:
    cleanup_containers()
    shutil.rmtree(WORK, ignore_errors=True)


def reported_version() -> str:
    """What the pinned package says it is (`promptfoo --version`)."""
    home = WORK / "version"
    (home / "config").mkdir(parents=True, exist_ok=True)
    cmd = promptfoo_cmd("", home / "unused.json", home, "version")
    cmd = cmd[: cmd.index(f"promptfoo@{PROMPTFOO_VERSION}") + 1] + ["--version"]
    try:
        proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=600)
    finally:
        cleanup_containers()
    return (proc.stdout.strip().splitlines() or [proc.stderr.strip()[-200:]])[-1]


def on_signal(signum: int, _frame: Any) -> None:
    raise SystemExit(128 + signum)


def promptfoo_cmd(config: str, out: Path, home: Path, tag: str) -> list[str]:
    args = ["eval", "-c", f"promptfoo/{config}", "-o", str(out), "--no-cache", "-j", "1",
            "--no-table", "--no-progress-bar", "--no-share"]
    env = {
        "PROMPTFOO_CONFIG_DIR": str(home / "config"),
        "PROMPTFOO_DISABLE_TELEMETRY": "1",
        "PROMPTFOO_DISABLE_UPDATE": "1",
        "npm_config_update_notifier": "false",
    }
    if USE_HOST:
        return ["env", *(f"{k}={v}" for k, v in env.items()),
                "npx", "-y", f"promptfoo@{PROMPTFOO_VERSION}", *args]
    (WORK / "home").mkdir(exist_ok=True)
    name = f"hb-promptfoo-{os.getpid()}-{tag}"
    CONTAINERS.add(name)
    cmd = ["docker", "run", "--rm", "--name", name, "--network", "host",
           "--cpus", "2", "--memory", "2g", "--user", f"{os.getuid()}:{os.getgid()}",
           "-v", f"{HERE}:{HERE}", "-w", str(HERE),
           "-e", f"HOME={WORK / 'home'}", "-e", f"npm_config_cache={WORK / 'npm'}"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    return [*cmd, NODE_IMAGE, "npx", "-y", f"promptfoo@{PROMPTFOO_VERSION}", *args]


def start_agent(defect: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "HB_DEFECT": defect[:2], "HB_PORT": str(PORT)}
    err = WORK / f"agent-{defect}.err"  # kept for a failed start, removed with WORK
    with err.open("wb") as sink:
        proc = subprocess.Popen([sys.executable, AGENT], cwd=HERE, env=env,
                                stdout=subprocess.DEVNULL, stderr=sink)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as r:
                if r.status == 200:
                    return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"agent did not come up on port {PORT}: {err.read_text()[-800:]}")


def stop_agent(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def verdict_of(report: Path, test: str) -> tuple[str, str]:
    data = json.loads(report.read_text())
    for row in data.get("results", {}).get("results", []):
        desc = row.get("testCase", {}).get("description") or row.get("description")
        if desc != test:
            continue
        label = "pass" if row.get("success") else FAILURE_REASON.get(row.get("failureReason"), "unknown")
        grading = row.get("gradingResult") or {}
        why = row.get("error") or grading.get("reason") or ""
        return label, str(why)[:600]
    return "unknown", f"no result row for {test!r} in {report.name}"


def run_cell(defect: str) -> dict[str, Any]:
    config, test = CELLS[defect]
    verdicts: Counter[str] = Counter()
    exits: Counter[int] = Counter()
    first: dict[str, str] = {}
    control: Counter[str] = Counter()
    control_first: dict[str, str] = {}
    for i in range(N):
        run_dir = WORK / f"{defect}-{i}"
        (run_dir / "config").mkdir(parents=True)
        report = run_dir / "out.json"
        cmd = promptfoo_cmd(config, report, run_dir, f"{defect.lower()}-{i}")
        agent = start_agent(defect)
        try:
            proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=600)
        finally:
            stop_agent(agent)
            cleanup_containers()
        exits[proc.returncode] += 1
        tail = (proc.stdout + proc.stderr).strip()[-600:]
        if report.exists():
            label, why = verdict_of(report, test)
        elif proc.returncode != 0:
            label, why = "refused", tail
        else:
            label, why = "unknown", tail
        if label == "unknown":
            why = f"{why} | {tail}"[:600]
        verdicts[label] += 1
        first.setdefault(label, why)
        if OTEL:
            c_label, c_why = verdict_of(report, CONTROL) if report.exists() else ("refused", tail)
            control[c_label] += 1
            control_first.setdefault(c_label, c_why)
        shutil.rmtree(run_dir, ignore_errors=True)
    cell: dict[str, Any] = {"verdicts": dict(verdicts),
                            "exit_codes": {str(k): v for k, v in exits.items()}, "first": first}
    if OTEL:
        cell["control"] = {"verdicts": dict(control), "first": control_first}
    return cell


def main() -> None:
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    out: dict[str, Any] = {
        "tool": "promptfoo",
        "channel": CHANNEL,
        "version": PROMPTFOO_VERSION,
        "runtime": "host npx" if USE_HOST else f"docker {NODE_IMAGE}",
        "n": N,
        "cells": {},
    }
    started = time.time()
    try:
        out["version_reported"] = reported_version()
        for defect in DEFECTS:
            t0 = time.time()
            cell = run_cell(defect)
            cell["seconds"] = round(time.time() - t0, 1)
            out["cells"][defect] = cell
            print(f"# {defect} done in {cell['seconds']}s: {cell['verdicts']}", file=sys.stderr, flush=True)
    finally:
        cleanup()
    out["seconds"] = round(time.time() - started, 1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
