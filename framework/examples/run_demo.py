#!/usr/bin/env python3
"""
Demo CLI: defines a small set of attacks, connects to a running device
simulator over TCP, and runs the full select -> run -> extract flow.

Start a simulator first (from simulator/, under WSL/Linux):
    make && ./simulator --port 9000

Then, from the repo root:
    python framework/examples/run_demo.py --host 127.0.0.1 --port 9000
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # let `orchestrator` import work unpacked

from orchestrator import (  # noqa: E402
    Attack,
    AttackCategory,
    AttackOrchestrator,
    CategoryAwareSelector,
    DeviceRequirement,
    NoCompatibleAttackError,
    Stage,
)
from orchestrator.transport.tcp import TCPDeviceClient  # noqa: E402


def build_attacks() -> List[Attack]:
    """
    Four attacks with deliberately different shapes and categories, to
    exercise both probability-based and category-aware selection. With
    CategoryAwareSelector() (its default ranking is cost-conscious), the
    expensive zero-day is tried last, only once the cheaper n-day/other
    candidates have each been attempted and failed.

    zero_day_unlock reuses the "elevate" stage id (also used inside
    full_bypass_chain, as its own independent chain) rather than a novel
    id, since the C simulator only recognizes a fixed built-in set of
    stage ids (see simulator/src/device_state.c) -- an unrecognized id
    gets rejected with ERR unknown_stage when actually run against it.
    """
    return [
        Attack(
            name="fast_pair_unlock",
            stages=[
                Stage("pair", "pair with device", 0.95),
                Stage("fast_pair", "fast-pair bypass", 0.9),
            ],
            requirement=DeviceRequirement(min_ios=(15, 0), min_battery=30),
            priority=1,
            category=AttackCategory.N_DAY,
        ),
        Attack(
            name="full_bypass_chain",
            stages=[
                Stage("pair", "pair with device", 0.95),
                Stage("bypass_lock", "bypass lock screen", 0.7),
                Stage("elevate", "elevate privileges", 0.6),
            ],
            requirement=DeviceRequirement(min_battery=20),
            category=AttackCategory.N_DAY,
        ),
        Attack(
            name="brute_force_pin",
            stages=[Stage("brute_pin", "brute-force PIN", 0.3)],
            requirement=DeviceRequirement(min_battery=50),
            priority=-1,
            category=AttackCategory.OTHER,
        ),
        Attack(
            name="zero_day_unlock",
            stages=[Stage("elevate", "zero-day exploit", 0.99)],
            requirement=DeviceRequirement(min_battery=10),
            category=AttackCategory.ZERO_DAY,
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--dest", default="extracted", help="directory to extract files into")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show real-time orchestrator log output"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")

    orchestrator = AttackOrchestrator(build_attacks(), selector=CategoryAwareSelector())
    client = TCPDeviceClient(args.host, args.port)
    try:
        state = client.get_state()
        print(
            f"device: model={state.model} ios={state.ios_version_str} "
            f"battery={state.battery_level}%"
        )

        try:
            result = orchestrator.run(client)
        except NoCompatibleAttackError:
            print("no compatible attack for this device")
            return 1

        for attempt in result.attempts:
            if attempt.succeeded:
                status = "OK"
            else:
                status = f"FAILED at {attempt.failed_stage_id} ({attempt.reason})"
            note = " [recovered from a dropped connection]" if attempt.reconnected else ""
            print(f"  attempt: {attempt.attack.name} -> {status}{note}")

        if not result.succeeded:
            print("all candidate attacks failed")
            return 1

        print(f"unlocked via '{result.successful_attack.name}', extracting to {args.dest}/")
        report = result.session.extract_all(args.dest, root="/")
        print(f"extracted {len(report.files_written)} file(s)")
        for path in report.files_written:
            print(f"  + {path}")
        for path, error in report.errors.items():
            print(f"  ! {path}: {error}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
