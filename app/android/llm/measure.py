"""Measure the phone's model, one run at a time, letting the phone cool between runs.

    python app/android/llm/measure.py --serial SERIAL --out FILE.jsonl [--package app.poolhouse.phone] [--only NAME ...]

Needs a debug build of the app on the phone and the models downloaded in it (Phone models screen). It starts the module's
instrumentation, which stops the app while it runs; it changes no setting of the phone, and it waits while the phone is
warm, low on battery or in battery saver. Each line of FILE is one JSON object: the run's name and a line the run printed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RUNNER = "app.poolhouse.llm.bench.BenchRunner"
E2B = "gemma-4-e2b-litert"
E4B = "gemma-4-e4b-litert"
WARMEST_C = 38.0
WARMEST_STATUS = 1
LEAST_BATTERY = 30
LONGEST_WAIT_S = 1800


def run_of(name: str, model: str, action: str, context: int, **extras: str) -> tuple[str, dict[str, str]]:
    return name, {"action": action, "model": model, "context": str(context), **extras}


RUNS = [
    run_of("e2b-16k-draft", E2B, "speed", 16384),
    run_of("e2b-16k-plain", E2B, "speed", 16384, drafting="no"),
    run_of("e2b-16k-private-storage", E2B, "speed", 16384, **{"from": "private"}),
    run_of("e2b-16k-copy-draft", E2B, "quality", 16384),
    run_of("e2b-16k-copy-int16-logits", E2B, "quality", 16384, activation="int16", drafting="no"),
    run_of("e2b-8k-draft", E2B, "speed", 8192),
    run_of("e2b-32k-draft", E2B, "speed", 32000),
    run_of("e2b-16k-sustained", E2B, "sustain", 16384, seconds="600"),
    run_of("e4b-16k-draft", E4B, "speed", 16384),
    run_of("e4b-16k-copy", E4B, "quality", 16384),
]


def adb(serial: str, *command: str, timeout: int = 60) -> str:
    done = subprocess.run(["adb", "-s", serial, *command], capture_output=True, text=True, timeout=timeout, check=False)
    return done.stdout + done.stderr


def condition(battery: str, thermal: str, saver: str) -> dict[str, float]:
    """The phone's heat and charge, from what `dumpsys battery`, `dumpsys thermalservice` and the saver setting print."""
    def number(pattern: str, text: str) -> int:
        found = re.search(pattern, text)
        if not found:
            raise ValueError(f"The phone did not say {pattern!r}.")
        return int(found.group(1))

    return {"level": number(r"\blevel: (\d+)", battery), "celsius": number(r"\btemperature: (\d+)", battery) / 10,
            "charging": float(bool(re.search(r"\b(AC|USB|Wireless|Dock) powered: true", battery))),
            "status": number(r"Thermal Status: (\d+)", thermal), "saver": float(saver.strip() == "1")}


def ready(now: dict[str, float]) -> bool:
    return (now["celsius"] <= WARMEST_C and now["status"] <= WARMEST_STATUS and not now["saver"]
            and (now["level"] >= LEAST_BATTERY or bool(now["charging"])))


def wait_until_ready(serial: str) -> dict[str, float]:
    waited = 0
    while True:
        now = condition(adb(serial, "shell", "dumpsys", "battery"), adb(serial, "shell", "dumpsys", "thermalservice"),
                        adb(serial, "shell", "settings", "get", "global", "low_power"))
        if ready(now):
            return now
        if waited >= LONGEST_WAIT_S:
            raise TimeoutError(f"The phone was not ready after {waited} s: {now}")
        print(f"waiting: {now}", flush=True)
        time.sleep(60)
        waited += 60


def lines(output: str) -> list[dict]:
    """The JSON of every `BENCH` line a run printed."""
    return [json.loads(line.split("BENCH ", 1)[1]) for line in output.splitlines() if "BENCH {" in line]


def measure(serial: str, package: str, extras: dict[str, str]) -> list[dict]:
    command = ["shell", "am", "instrument", "-w", "-r"]
    for key, value in extras.items():
        command += ["-e", key, value]
    seconds = 900 + int(extras.get("seconds", "0"))
    printed = lines(adb(serial, *command, f"{package}/{RUNNER}", timeout=seconds))
    # A run the system ended says nothing; the app's exit reasons say why.
    if not any(line.get("event") in ("memory", "downloaded") for line in printed):
        printed += lines(adb(serial, "shell", "am", "instrument", "-w", "-r", "-e", "action", "info", f"{package}/{RUNNER}", timeout=120))
    return printed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python app/android/llm/measure.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--package", default="app.poolhouse.phone")
    parser.add_argument("--only", nargs="+", choices=[name for name, _ in RUNS])
    asked = parser.parse_args(argv)
    for name, extras in RUNS:
        if asked.only and name not in asked.only:
            continue
        before = wait_until_ready(asked.serial)
        print(f"run: {name} {before}", flush=True)
        printed = measure(asked.serial, asked.package, extras)
        with asked.out.open("a", encoding="utf-8") as out:
            for line in [{"event": "before", **before, **extras}, *printed]:
                out.write(json.dumps({"run": name, **line}) + "\n")
    print(f"done: {asked.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
