"""CLI.
  python -m engagement_runner --demo --suite cart_abandonment
  python -m engagement_runner --json flow.json --random 20
  python -m engagement_runner --flow-id <uuid> [--version 2]
  python -m engagement_runner --tenant <uuid> --name "Cart abandonment"
"""
from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID

from .engine import FlowEngine
from .repository import FlowRepository, SampleProfileSource
from .settings import get_settings
from .uat import CART_ABANDONMENT, UatRunner, cart_abandonment_cases, print_journeys, print_uat


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="engagement_runner")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true", help="built-in cart abandonment flow, no database")
    src.add_argument("--json", help="workflow JSON file exported from the studio")
    src.add_argument("--flow-id", type=UUID)
    src.add_argument("--name", help="flow name (needs --tenant)")
    ap.add_argument("--tenant", type=UUID)
    ap.add_argument("--version", type=int, help="default: live version, else latest")
    ap.add_argument("--random", type=int, default=0, help="add N random sample profiles")
    ap.add_argument("--suite", choices=["cart_abandonment"], help="run the built-in UAT assertions")
    args = ap.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    if args.demo:
        definition = CART_ABANDONMENT
    elif args.json:
        definition = FlowRepository.load_file(args.json)
    else:
        repo = FlowRepository.from_settings(settings)
        flow_id = args.flow_id or repo.find_flow_id(args.tenant, args.name)
        definition = repo.load_definition(flow_id, args.version)

    engine = FlowEngine(definition, max_steps=settings.max_steps_per_journey)
    problems = engine.problems()
    if problems:
        print("Flow has config problems:\n  - " + "\n  - ".join(problems))
        return 2

    if args.suite:
        return print_uat(UatRunner(engine).run(cart_abandonment_cases()))
    print_journeys(engine.run_many(SampleProfileSource(extra_random=args.random).fetch()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
