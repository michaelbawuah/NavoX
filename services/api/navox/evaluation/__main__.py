import argparse
import json
from pathlib import Path

from navox.evaluation.runner import markdown_report, run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the NavoX Milestone 9 evaluation gate")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    parser.add_argument("--markdown", type=Path, help="Write the Markdown report to this path")
    parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="Return a non-zero exit code when any release gate fails",
    )
    args = parser.parse_args()

    report = run_evaluation()
    serialized = json.dumps(report, indent=2, sort_keys=True)
    print(serialized)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown_report(report), encoding="utf-8")

    return 1 if args.fail_on_gate and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
