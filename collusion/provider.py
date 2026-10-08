"""OpenRouter transport, metadata preflight and an explicitly artificial mock."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from urllib import request, error
from .task import Public, fixed_report

BASE = "https://openrouter.ai/api/v1"


@dataclass
class Completion:
    text: str
    metadata: dict


def load_key(path=Path(".env")):
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key and path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name == "OPENROUTER_API_KEY":
                key = value.strip().strip("\"'")
    if not key:
        raise ValueError("Set OPENROUTER_API_KEY in the environment or local .env")
    return key


def model_catalog():
    req = request.Request(BASE + "/models", headers={"User-Agent": "collusion-pilot/0.1"})
    with request.urlopen(req, timeout=30) as response:
        return json.load(response)["data"]


def preflight(config):
    matches = [m for m in model_catalog() if m["id"] == config["model"]]
    if not matches:
        raise ValueError(f"Requested model {config['model']} is not listed. "
                         "No automatic model substitution is allowed.")
    model = matches[0]
    supported = model.get("supported_parameters", [])
    required = ["reasoning", "max_tokens"]
    if config["temperature"] is not None:
        required.append("temperature")
    if config.get("api_seed") is not None:
        required.append("seed")
    missing = [p for p in required if p not in supported]
    if missing:
        raise ValueError(f"Model metadata does not advertise required parameters: {missing}")
    reasoning = model.get("reasoning", {})
    efforts = reasoning.get("supported_efforts")
    if efforts is not None and config["reasoning_effort"] not in efforts:
        raise ValueError("Requested reasoning effort is not supported by model metadata")
    if reasoning.get("mandatory") and config["reasoning_effort"] == "none":
        raise ValueError("Model requires reasoning; cannot run the none condition")
    return {"model": model, "effort_verified_by_catalog": efforts is not None,
            "note": "Metadata preflight is not proof of account access or a paid completion."}


class OpenRouter:
    name = "openrouter"

    def __init__(self, config, key):
        self.config = config
        self._key = key

    def complete(self, messages):
        c = self.config
        routing = {"allow_fallbacks": False, "require_parameters": True}
        if c["provider_order"]:
            routing["order"] = c["provider_order"]
        payload = {"model": c["model"], "messages": messages,
                   "max_tokens": c["max_tokens"],
                   "reasoning": {"effort": c["reasoning_effort"]}, "provider": routing}
        if c["temperature"] is not None:
            payload["temperature"] = c["temperature"]
        if c["api_seed"] is not None:
            payload["seed"] = c["api_seed"]
        start = time.monotonic()
        for attempt in range(c["max_retries"] + 1):
            req = request.Request(BASE + "/chat/completions",
                                  data=json.dumps(payload).encode(),
                                  headers={"Authorization": "Bearer " + self._key,
                                           "Content-Type": "application/json"})
            try:
                with request.urlopen(req, timeout=c["timeout_seconds"]) as response:
                    result = json.load(response)
                if "error" in result:
                    raise RuntimeError("Provider returned an error object; response body omitted")
                choices = result.get("choices", [])
                if not choices:
                    raise RuntimeError("Provider returned no completion choices")
                choice = choices[0]
                content = choice.get("message", {}).get("content")
                if content is None:
                    content = ""  # Refusal/empty text is scored, not retried.
                if not isinstance(content, str):
                    raise RuntimeError("Unexpected provider content type")
                meta = {k: result.get(k) for k in
                        ("id", "model", "provider", "usage", "system_fingerprint", "created")}
                meta.update(finish_reason=choice.get("finish_reason"),
                            requested_model=c["model"], retries=attempt,
                            elapsed_seconds=time.monotonic() - start)
                # Do not retain headers, credentials or intermediate reasoning.
                return Completion(content, meta)
            except error.HTTPError as exc:
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == c["max_retries"]:
                    raise RuntimeError(f"OpenRouter HTTP {exc.code}; body omitted") from None
            except (error.URLError, TimeoutError):
                if attempt == c["max_retries"]:
                    raise RuntimeError("OpenRouter transport failed; run can be resumed") from None
            time.sleep(min(2 ** attempt, 8))
        raise RuntimeError("Unreachable retry state")


class Mock:
    """Always-public senders and abstaining C; oracle lookup only. NOT LLM evidence."""
    name = "mock"

    def complete(self, messages):
        view = json.loads(messages[-1]["content"])
        if "role" in view:
            text = fixed_report(Public(**view["public"]))
        elif "registry" in view:
            text = json.dumps({"public_total": view["completed_A"] + view["completed_B"],
                               "private_label": view["registry"][view["case_code"]]})
        else:
            import re
            total = 0
            for field in ("report_A", "report_B"):
                match = re.search(r"(?:We completed |Completed tickets: )(\d+)", view[field])
                total += int(match[1])
            text = json.dumps({"public_total": total, "private_label": None})
        return Completion(text, {"model": "MOCK-NOT-A-MODEL", "provider": "local",
                                 "usage": None, "finish_reason": "stop", "retries": 0})
