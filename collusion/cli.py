import argparse
import json
from pathlib import Path
import sys
from .analysis import summarize
from .provider import Mock, OpenRouter, load_key, preflight
from .runner import load_config, run
from .task import validate_environment


def main():
    parser = argparse.ArgumentParser(description="Unauthorized joint-disclosure pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Offline exhaustive environment checks")
    for name in ("plan", "preflight", "run"):
        p = sub.add_parser(name)
        p.add_argument("--config", default="configs/pilot.json")
        if name in ("plan", "run"):
            p.add_argument("--limit", type=int, choices=range(1, 97), default=96, metavar="1..96")
        if name == "run":
            p.add_argument("--backend", choices=("mock", "openrouter"), default="mock")
            p.add_argument("--output", required=True)
    p = sub.add_parser("summarize")
    p.add_argument("directory")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            result = validate_environment()
        elif args.command == "summarize":
            manifest = json.loads((Path(args.directory) / "manifest.json").read_text(encoding="utf-8"))
            result = summarize(args.directory, manifest["config"]["bootstrap_samples"],
                               manifest["config"]["bootstrap_seed"])
        else:
            config = load_config(args.config)
            if args.command == "plan":
                result = {"model": config["model"], "states": args.limit,
                          "calls": 13 * args.limit, "api_calls_made": 0,
                          "note": "API model availability is checked by preflight, not plan"}
            elif args.command == "preflight":
                result = preflight(config)
            else:
                check = preflight(config) if args.backend == "openrouter" else None
                backend = OpenRouter(config, load_key()) if check is not None else Mock()
                result = run(config, backend, args.output, args.limit, check)
                result = {"backend": result["backend"], "output": args.output,
                          "cached_completions": result["cached_completions"],
                          "is_model_evidence": result["is_model_evidence"]}
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
