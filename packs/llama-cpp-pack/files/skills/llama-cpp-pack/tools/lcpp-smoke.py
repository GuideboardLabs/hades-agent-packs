#!/usr/bin/env python3
"""Pre-test a served model before anyone relies on it.

Tuning finds the fastest flags. This finds out whether the thing actually works as an agent model:
does the chat template produce content, does the thinking budget leave room for an answer, do tool
calls come back as parseable JSON, does the context window survive a long prompt, and how fast is
decode. Those are the five failures that waste an afternoon and every one of them is cheap to test.

    ./lcpp-smoke.py --url http://127.0.0.1:8115     run every check
    ./lcpp-smoke.py --url ... --json smoke.json    keep the measurements
    ./lcpp-smoke.py --url ... --only tools         one check

Exit status: 0 all checks pass, 1 a check failed, 2 the server could not be reached.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

NEEDLE = "ZX-4471"


def post(url: str, body: dict, timeout: int = 900) -> dict:
    request = urllib.request.Request(
        url + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def get(url: str, path: str, timeout: int = 10):
    try:
        with urllib.request.urlopen(url + path, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def host_load() -> dict:
    """1-minute load average against core count: a speed number taken under contention is not a
    speed number, it is a description of the contention."""
    try:
        with open("/proc/loadavg") as handle:
            one, _, _ = handle.read().split()[:3]
        cores = __import__("os").cpu_count() or 1
        return {"load1": float(one), "cores": cores, "per_core": round(float(one) / cores, 2)}
    except Exception:
        return {}


def timings_of(reply: dict) -> dict:
    usage = reply.get("usage") or {}
    timings = reply.get("timings") or {}
    prompt_tokens = usage.get("prompt_tokens") or 0
    completion_tokens = usage.get("completion_tokens") or 0
    prompt_per_second = timings.get("prompt_per_second")
    predicted_per_second = timings.get("predicted_per_second")
    elapsed = reply.get("_elapsed")
    if predicted_per_second is None and completion_tokens and elapsed:
        predicted_per_second = round(completion_tokens / elapsed, 2)
    if prompt_per_second is None and prompt_tokens and elapsed:
        prompt_per_second = round(prompt_tokens / elapsed, 2)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prefill_tps": round(prompt_per_second, 1) if prompt_per_second else None,
        "decode_tps": round(predicted_per_second, 2) if predicted_per_second else None,
        "wall_s": round(elapsed, 1) if elapsed else None,
    }


def message_of(reply: dict) -> dict:
    choices = reply.get("choices") or [{}]
    return choices[0].get("message") or {}


def run_check(name: str, fn, url: str, results: list) -> None:
    print(f"... {name}", flush=True)
    try:
        verdict = fn()
    except Exception as error:
        verdict = {"check": name, "status": "fail", "detail": f"raised {error!r}"}
    results.append(verdict)
    label = {"pass": "PASS", "fail": "FAIL", "warn": "warn"}.get(verdict["status"], verdict["status"])
    print(f"[{label}] {name}: {verdict.get('detail', '')}")
    if verdict.get("fix"):
        print(f"       fix: {verdict['fix']}")


def make_checks(url: str, args) -> dict:
    props = get(url, "/props") or {}
    served = (props.get("default_generation_settings") or {}).get("n_ctx") or args.ctx
    model_name = props.get("model_path") or props.get("model") or "unknown"

    def basics():
        if not props:
            return {"check": "basics", "status": "fail", "detail": "no /props response",
                    "fix": "the server is not a llama-server or not listening on that port"}
        return {"check": "basics", "status": "pass",
                "detail": f"model {model_name}, n_ctx {served}, slots {props.get('total_slots') or len(props.get('slots') or []) or 'unreported'}",
                "served_ctx": served}

    def template():
        started = time.time()
        reply = post(url, {"messages": [{"role": "system", "content": "You are terse."},
                                        {"role": "user", "content": "Reply with the single word: ready"}],
                          "max_tokens": 32, "temperature": 0})
        reply["_elapsed"] = time.time() - started
        message = message_of(reply)
        content = (message.get("content") or "").strip()
        finish = (reply.get("choices") or [{}])[0].get("finish_reason")
        if content:
            return {"check": "template", "status": "pass",
                    "detail": f"content {content[:40]!r}, finish {finish}", **timings_of(reply)}
        return {"check": "template", "status": "fail",
                "detail": f"empty content, finish {finish}",
                "fix": "the chat template produced nothing: check --chat-template, and check whether the model spent the budget thinking"}

    def thinking():
        started = time.time()
        reply = post(url, {"messages": [{"role": "user",
                                         "content": "How many integers between 1 and 100 are divisible by 3 or 5? Show the reasoning."}],
                           "max_tokens": args.thinking_budget, "temperature": 0.7})
        reply["_elapsed"] = time.time() - started
        message = message_of(reply)
        content = (message.get("content") or "").strip()
        reasoning = (message.get("reasoning_content") or "").strip()
        finish = (reply.get("choices") or [{}])[0].get("finish_reason")
        detail = (f"{len(reasoning)} chars reasoning, {len(content)} chars answer, "
                  f"finish {finish}, budget {args.thinking_budget}")
        if finish == "length" and not content:
            return {"check": "thinking_budget", "status": "fail", "detail": detail,
                    "fix": f"a thinking model needs more than {args.thinking_budget} tokens: set max_tokens 2048+ or disable thinking"}
        return {"check": "thinking_budget", "status": "pass", "detail": detail, **timings_of(reply)}

    def tools():
        body = {
            "messages": [{"role": "user", "content": "What is the weather in Paris? Call the tool, do not guess."}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Current weather for a city",
                    "parameters": {"type": "object",
                                   "properties": {"city": {"type": "string"}, "unit": {"type": "string"}},
                                   "required": ["city"]},
                },
            }],
            "tool_choice": "auto",
            "max_tokens": 512,
            "temperature": 0,
        }
        started = time.time()
        reply = post(url, body)
        reply["_elapsed"] = time.time() - started
        message = message_of(reply)
        calls = message.get("tool_calls") or []
        if not calls:
            return {"check": "tools", "status": "fail",
                    "detail": f"no tool_calls; content was {(message.get('content') or '')[:60]!r}",
                    "fix": "this build or template cannot emit tool calls: use --chat-template or avoid this model for agent work"}
        call = calls[0].get("function") or {}
        arguments = call.get("arguments")
        parse_error = ""
        if isinstance(arguments, str):
            try:
                json.loads(arguments)
                parsed = True
            except json.JSONDecodeError as error:
                parsed = False
                parse_error = str(error)
        else:
            parsed = isinstance(arguments, dict)
            parse_error = "arguments were not a string or object"
        if not parsed:
            return {"check": "tools", "status": "fail",
                    "detail": f"name {call.get('name')!r}, arguments not valid JSON: {parse_error}",
                    "fix": "usually a truncated generation: raise max_tokens, raise context, or the turn was cut mid-string"}
        return {"check": "tools", "status": "pass",
                "detail": f"name {call.get('name')!r}, arguments parse as JSON ({len(str(arguments))} chars)",
                **timings_of(reply)}

    def long_context():
        target = int(served * args.context_fraction) if served else 4096
        filler = "The archive entry is a record of sensor readings taken on a clear night. " * max(1, target // 15)
        prompt = (filler + f"\n\nRemember this: the access code is {NEEDLE}. "
                  "\n\nWhat is the access code? Answer with the code only.")
        started = time.time()
        try:
            reply = post(url, {"messages": [{"role": "user", "content": prompt}],
                               "max_tokens": 32, "temperature": 0})
        except urllib.error.HTTPError as error:
            body = error.read().decode(errors="replace")[:200]
            return {"check": "long_context", "status": "fail",
                    "detail": f"HTTP {error.code} at ~{target} tokens: {body}",
                    "fix": "the declared context is larger than the server's: raise -c or lower the client's context_tokens"}
        reply["_elapsed"] = time.time() - started
        content = (message_of(reply).get("content") or "")
        found = NEEDLE in content
        timings = timings_of(reply)
        if not found:
            return {"check": "long_context", "status": "fail",
                    "detail": f"prompt {timings['prompt_tokens']} tokens, needle not returned ({content[:40]!r})",
                    "fix": "either the prompt did not reach the model (silent truncation) or attention failed at depth; re-run with fewer tokens to find the cliff"}
        return {"check": "long_context", "status": "pass",
                "detail": f"needle found at {timings['prompt_tokens']} prompt tokens, {timings['decode_tps']} t/s", **timings}

    def speed():
        prompt = ("Write a detailed plan for testing a local language model on a small GPU, "
                  "covering fit, context, quantisation and measurement. Be concrete.")
        started = time.time()
        reply = post(url, {"messages": [{"role": "user", "content": prompt}],
                           "max_tokens": args.speed_tokens, "temperature": 0.7})
        reply["_elapsed"] = time.time() - started
        timings = timings_of(reply)
        decode = timings["decode_tps"] or 0
        load = host_load()
        busy = load.get("per_core", 0) > 1.2
        if busy:
            return {"check": "speed", "status": "warn",
                    "detail": f"{decode} t/s decode, but load is {load['load1']} on {load['cores']} cores "
                              f"({load['per_core']} per core): this measures contention, not the model",
                    "fix": "re-run on an idle machine before quoting any of these numbers", **timings}
        if decode < args.floor:
            return {"check": "speed", "status": "fail",
                    "detail": f"{decode} t/s decode over {timings['completion_tokens']} tokens, floor {args.floor}",
                    "fix": "below the pack's admission floor: this is a reference row, not a usable model", **timings}
        return {"check": "speed", "status": "pass",
                "detail": f"{decode} t/s decode, {timings['prefill_tps']} t/s prefill, "
                          f"{timings['prompt_tokens']} prompt tokens", **timings}

    return {"basics": basics, "template": template, "thinking": thinking, "tools": tools,
            "long_context": long_context, "speed": speed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-test a served model.")
    parser.add_argument("--url", default="http://127.0.0.1:8115")
    parser.add_argument("--only", help="comma separated: basics,template,thinking,tools,long_context,speed")
    parser.add_argument("--thinking-budget", type=int, default=2048)
    parser.add_argument("--speed-tokens", type=int, default=192)
    parser.add_argument("--floor", type=float, default=8.0, help="decode t/s below which the model is reference only")
    parser.add_argument("--context-fraction", type=float, default=0.55)
    parser.add_argument("--ctx", type=int, default=8192, help="fallback when /props has no n_ctx")
    parser.add_argument("--json", help="write results here")
    args = parser.parse_args()
    url = args.url.rstrip("/")

    checks = make_checks(url, args)
    wanted = [c.strip() for c in args.only.split(",")] if args.only else list(checks)
    results = []
    for name in wanted:
        if name not in checks:
            print(f"unknown check {name}", file=sys.stderr)
            return 2
        run_check(name, checks[name], url, results)

    failed = [r for r in results if r["status"] == "fail"]
    warned = [r for r in results if r["status"] == "warn"]
    passed = [r for r in results if r["status"] == "pass"]
    print()
    if results:
        line = f"{len(passed)} of {len(results)} checks passed"
        if warned:
            line += f", {len(warned)} warned ({', '.join(r['check'] for r in warned)})"
        if failed:
            line += f", {len(failed)} failed ({', '.join(r['check'] for r in failed)})"
        print(line)
    if args.json:
        with open(args.json, "w") as handle:
            json.dump({"url": url, "results": results}, handle, indent=2)
        print(f"written: {args.json}")
    if not results:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
