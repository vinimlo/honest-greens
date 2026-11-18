"""Honest greens, D3b attached: highbar against an agent that is already running.

The reference agent (agent_http.py, HB_DEFECT=D3, port 8745) is started once and keeps
running for every run, writing its own trace file (attached-traces.jsonl), as a deployed
service does. One run is one `highbar run` of specs/guardrail_last_attached.highbar.yaml
in attach mode, so highbar never stops the agent. The forbidden call lands
HB_LATE_SECONDS after the reply of the last test. After each run the harness waits for
the late call and counts it in the trace file, so the cell also says whether the call
was made, not only whether highbar saw it.

Prints one JSON document to stdout. Nothing calls a model.
"""

from __future__ import annotations

import json
import os
import shutil
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
PORT = 8745
SPEC = "specs/guardrail_last_attached.highbar.yaml"
TEST = "never deletes an account from chat"
TRACES = HERE / "attached-traces.jsonl"


def deletes_in_trace() -> int:
    if not TRACES.exists():
        return 0
    return sum(1 for line in TRACES.read_text().splitlines() if '"delete_account"' in line)


def main() -> None:
    TRACES.unlink(missing_ok=True)
    env = {**os.environ, "HB_DEFECT": "D3", "HB_PORT": str(PORT),
           "HB_LATE_SECONDS": str(LATE), "HIGHBAR_TRACES_URL": str(TRACES)}
    agent = subprocess.Popen([sys.executable, "agent_http.py"], cwd=HERE, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    verdicts: Counter[str] = Counter()
    exits: Counter[int] = Counter()
    first: dict[str, str] = {}
    made: Counter[str] = Counter()
    watched: Counter[str] = Counter()
    try:
        deadline = time.time() + 20
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1):
                    break
            except OSError:
                if time.time() > deadline:
                    raise RuntimeError("agent did not come up") from None
                time.sleep(0.1)
        exe = shutil.which("highbar") or "highbar"
        for _ in range(N):
            before = deletes_in_trace()
            with tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp) / "r.json"
                proc = subprocess.run([exe, "run", SPEC, "--report", f"json={report}"],
                                      cwd=HERE, capture_output=True, text=True, timeout=120)
                exits[proc.returncode] += 1
                verdict, why = "unknown", (proc.stdout + proc.stderr).strip()[-600:]
                if report.exists():
                    data = json.loads(report.read_text())
                    for s in data.get("specs", []):
                        for t in s.get("tests", []):
                            if t.get("name") == TEST:
                                verdict = t.get("status", "unknown")
                                why = json.dumps(t.get("errors"))[:600]
                                if "watched_ms" in t:
                                    watched[str(t["watched_ms"])] += 1
                verdicts[verdict] += 1
                first.setdefault(verdict, why)
            time.sleep(LATE + 1.0)  # the late call lands before the next run starts
            made["made" if deletes_in_trace() > before else "not made"] += 1
    finally:
        agent.terminate()
        try:
            agent.wait(timeout=5)
        except subprocess.TimeoutExpired:
            agent.kill()
        TRACES.unlink(missing_ok=True)
    print(json.dumps({
        "n": N, "highbar": version("highbar"), "late_seconds": LATE, "mode": "attached",
        "verdicts": dict(verdicts), "exit_codes": {str(k): v for k, v in exits.items()},
        "late_call": dict(made), "watched_ms": dict(watched), "first": first,
    }, indent=1))


if __name__ == "__main__":
    main()
