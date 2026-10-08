"""Resumable 13N-call pilot with cached source-message replacements."""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from . import prompts
from .analysis import summarize
from .task import worlds, fixed_report, deliver, score, validate_environment

CONDITIONS = (("authorized", True, True), ("unauthorized_neutral", False, False),
              ("unauthorized_incentivized", False, True))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def source_hash():
    return digest({p.name: p.read_text(encoding="utf-8")
                   for p in sorted(Path(__file__).parent.glob("*.py"))})


def load_config(path):
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = {"model", "reasoning_effort", "temperature", "max_tokens", "api_seed",
                "world_seed", "bootstrap_seed", "bootstrap_samples", "timeout_seconds",
                "max_retries", "provider_order"}
    if set(c) != expected:
        raise ValueError(f"Config keys mismatch: missing={expected-set(c)}, extra={set(c)-expected}")
    for k in ("max_tokens", "world_seed", "bootstrap_seed", "bootstrap_samples", "max_retries"):
        if type(c[k]) is not int or c[k] < (1 if k in ("max_tokens", "bootstrap_samples") else 0):
            raise ValueError(f"Invalid {k}")
    if c["api_seed"] is not None and type(c["api_seed"]) is not int:
        raise ValueError("api_seed must be an integer or null")
    if not isinstance(c["model"], str) or not c["model"]:
        raise ValueError("A model ID must be specified")
    if c["reasoning_effort"] != "none":
        raise ValueError("This pilot fixes reasoning_effort=none; use a separate protocol for thinking")
    if c["temperature"] is not None and (not isinstance(c["temperature"], (int, float))
                                         or not 0 <= c["temperature"] <= 2):
        raise ValueError("Invalid temperature")
    if not isinstance(c["timeout_seconds"], (int, float)) or c["timeout_seconds"] <= 0:
        raise ValueError("Invalid timeout")
    if not isinstance(c["provider_order"], list) or not all(isinstance(s, str) for s in c["provider_order"]):
        raise ValueError("provider_order must be a list of provider names")
    return c


class Cache:
    def __init__(self, root, backend):
        self.root, self.backend = root, backend

    def call(self, call_id, messages):
        path = self.root / "calls" / (call_id + ".json")
        fingerprint = digest(messages)
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["request_hash"] != fingerprint:
                raise ValueError("Cached request mismatch; use a new output directory")
            return record["text"]
        completion = self.backend.complete(messages)
        record = {"call_id": call_id, "request_hash": fingerprint, "messages": messages,
                  "text": completion.text, "metadata": completion.metadata,
                  "saved_at_utc": datetime.now(timezone.utc).isoformat()}
        atomic_json(path, record)
        return completion.text


def run(config, backend, output, limit=96, preflight_result=None):
    if not 1 <= limit <= 96:
        raise ValueError("limit must be between 1 and 96")
    validate_environment()
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    # Prevent two runners writing or billing the same run concurrently.
    lock = root / ".running"
    with lock.open("x", encoding="utf-8") as f:
        import os
        f.write(str(os.getpid()))
    try:
        return _run(config, backend, root, limit, preflight_result)
    finally:
        lock.unlink(missing_ok=True)


def _run(config, backend, root, limit, preflight_result):
    identity = {"config": config, "backend": backend.name, "world_count": limit,
                "source_hash": source_hash(), "prompt_version": prompts.VERSION}
    path = root / "manifest.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if any(old.get(k) != v for k, v in identity.items()):
            raise ValueError("Run configuration/source changed; choose a new directory")
    else:
        git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        atomic_json(path, {**identity, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                          "python": platform.python_version(),
                          "git_commit": git.stdout.strip() if git.returncode == 0 else None,
                          "planned_calls": 13 * limit, "preflight": preflight_result,
                          "subset_warning": limit != 96})
    for folder in ("calls", "episodes"):
        (root / folder).mkdir(exist_ok=True)
    cache = Cache(root, backend)
    for w in worlds(config["world_seed"])[:limit]:
        def record(condition, raw, unauthorized, source_messages=None):
            metrics = score(raw, w, unauthorized)
            metrics["source_invalid_rate"] = (sum(not m["valid"] for m in source_messages) / 2
                                               if source_messages else 0)
            atomic_json(root / "episodes" / f"{w.world_id}_{condition}.json",
                        {"world_id": w.world_id, "condition": condition, "world": w.record(),
                         "receiver_raw": raw, "source_messages": source_messages,
                         "scores": metrics})
        # Basic capability check first; no automatic stop based on observed accuracy.
        raw = cache.call(w.world_id + "_oracle_C", prompts.oracle_messages(w))
        record("oracle", raw, False)
        active = None
        for condition, auth, incentive in CONDITIONS:
            prefix = w.world_id + "_" + condition
            # Independent requests: neither prompt includes the partner's message.
            ra = cache.call(prefix + "_A", prompts.sender_messages(w, "A", auth, incentive))
            rb = cache.call(prefix + "_B", prompts.sender_messages(w, "B", auth, incentive))
            ma, mb = deliver(ra, w.public_a), deliver(rb, w.public_b)
            rc = cache.call(prefix + "_C", prompts.receiver_messages(ma["delivered"], mb["delivered"]))
            record(condition, rc, not auth, [ma, mb])
            if condition == "unauthorized_incentivized":
                active = (ma, mb)
        a, b = active
        a0, b0 = deliver(fixed_report(w.public_a), w.public_a), deliver(fixed_report(w.public_b), w.public_b)
        for name, ma, mb in (("replace_A", a0, b), ("replace_B", a, b0),
                             ("replace_both", a0, b0)):
            raw = cache.call(w.world_id + "_" + name + "_C",
                             prompts.receiver_messages(ma["delivered"], mb["delivered"]))
            record(name, raw, True, [ma, mb])
        print(f"Completed {w.world_id} ({backend.name})", flush=True)
    return summarize(root, config["bootstrap_samples"], config["bootstrap_seed"])
