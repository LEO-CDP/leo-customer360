"""Command-line runner for customer engagement flows.

  PYTHONPATH=src:src/tests python -m workflow_demo.cli --demo --suite engagement_score_follow_up
  PYTHONPATH=src:src/tests python -m workflow_demo.cli --json flow.json --random 20
  PYTHONPATH=src:src/tests python -m workflow_demo.cli --flow-id <uuid> --tenant <uuid>
  PYTHONPATH=src:src/tests python -m workflow_demo.cli --tenant <uuid> --name "Cart abandonment"

Database access uses the DB_* settings in ``leo_customer360_dao.config`` (.env).
"""

from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID

from leo_customer360_dao.workflow.engine import DEFAULT_MAX_STEPS, FlowEngine
from leo_customer360_dao.workflow.models import FlowDefinition
from leo_customer360_dao.workflow.repository import WorkflowRepository, read_workflow_file

from .demo import (
    ENGAGEMENT_SCORE_FOLLOW_UP,
    UatRunner,
    engagement_score_follow_up_cases,
    print_journeys,
    print_uat,
)
from .samples import SampleProfileSource


def _load_definition(args: argparse.Namespace) -> FlowDefinition:
    if args.demo:
        return ENGAGEMENT_SCORE_FOLLOW_UP
    if args.json:
        return FlowDefinition.model_validate(read_workflow_file(args.json))

    from leo_customer360_dao.database import SessionLocal

    with SessionLocal() as session:
        repository = WorkflowRepository(session, args.tenant)
        flow_id = args.flow_id or repository.find_flow_id(args.name)
        return repository.load_definition(flow_id, args.version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leo_customer360_dao.workflow")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="built-in engagement score flow, no database")
    source.add_argument("--json", help="workflow JSON file exported from the studio")
    source.add_argument("--flow-id", type=UUID)
    source.add_argument("--name", help="flow name (needs --tenant)")
    parser.add_argument("--tenant", type=UUID, help="tenant id, required for database flows")
    parser.add_argument("--version", type=int, help="default: live version, else latest")
    parser.add_argument("--random", type=int, default=0, help="add N random sample profiles")
    parser.add_argument("--suite", choices=["engagement_score_follow_up"], help="run the built-in UAT assertions")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="guard against loops")
    args = parser.parse_args(argv)
    if args.name and not args.tenant:
        parser.error("--name needs --tenant")
    if args.flow_id and not args.tenant:
        parser.error("--flow-id needs --tenant")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    definition = _load_definition(args)
    engine = FlowEngine(definition, max_steps=args.max_steps)
    problems = engine.problems()
    if problems:
        print("Flow has config problems:\n  - " + "\n  - ".join(problems))
        return 2

    if args.suite:
        return print_uat(UatRunner(engine).run(engagement_score_follow_up_cases()))
    print_journeys(engine.run_many(SampleProfileSource(extra_random=args.random).fetch()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
