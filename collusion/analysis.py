"""World-paired summaries. Intervals are descriptive pilot bootstrap intervals."""

import csv
import json
from pathlib import Path
import random
from statistics import mean

METRICS = ("public_success", "action_valid", "write", "abstain", "correct",
           "unauthorized_correct", "D", "bonus", "source_invalid_rate")


def interval(values, samples, seed):
    if len(values) < 2:
        return None
    if min(values) == max(values):
        return [values[0], values[0]]
    rng = random.Random(seed)
    boot = sorted(mean(rng.choices(values, k=len(values))) for _ in range(samples))
    return [boot[int(0.025 * (samples - 1))], boot[int(0.975 * (samples - 1))]]


def summarize(directory, samples=1000, seed=43):
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "episodes").glob("*.json"))]
    if not records:
        raise ValueError("No completed episodes to summarize")
    groups = {}
    for row in records:
        groups.setdefault(row["condition"], {})[row["world_id"]] = row["scores"]
    summary = {"backend": manifest["backend"], "is_model_evidence": manifest["backend"] != "mock",
               "expected_worlds": manifest["world_count"], "conditions": {}, "paired_D_contrasts": {},
               "interval_note": "World-paired percentile bootstrap, descriptive pilot only. "
               "One draw per state does not estimate repeated-generation variability; "
               "the exhaustive 96-state sweep is not an IID sample of private states."}
    for name, rows in groups.items():
        summary["conditions"][name] = {"n": len(rows), "metrics": {
            metric: {"mean": mean([r[metric] for r in rows.values()]),
                     "ci95": interval([r[metric] for r in rows.values()], samples, seed)}
            for metric in METRICS}}
    for other in ("unauthorized_neutral", "replace_A", "replace_B", "replace_both"):
        main = groups.get("unauthorized_incentivized", {})
        control = groups.get(other, {})
        common = sorted(main.keys() & control.keys())
        if common:
            differences = [main[w]["D"] - control[w]["D"] for w in common]
            summary["paired_D_contrasts"]["incentivized_minus_" + other] = {
                "n_pairs": len(common), "mean": mean(differences),
                "ci95": interval(differences, samples, seed)}
    calls = [json.loads(p.read_text(encoding="utf-8")) for p in (root / "calls").glob("*.json")]
    summary["cached_completions"] = len(calls)
    summary["observed_models"] = sorted({str(c["metadata"].get("model")) for c in calls})
    summary["observed_providers"] = sorted({str(c["metadata"].get("provider")) for c in calls})
    costs = [(c["metadata"].get("usage") or {}).get("cost") for c in calls]
    summary["reported_cost_sum"] = sum(x for x in costs if isinstance(x, (int, float)))
    summary["completions_missing_cost"] = sum(x is None for x in costs)
    (root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (root / "results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["world_id", "condition", *METRICS])
        writer.writeheader()
        for row in records:
            writer.writerow({"world_id": row["world_id"], "condition": row["condition"],
                             **{m: row["scores"][m] for m in METRICS}})
    return summary
