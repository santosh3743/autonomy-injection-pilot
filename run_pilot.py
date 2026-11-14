"""Pilot: how does prompt-injection risk change across five autonomy levels (L1-L5),
and what does each level cost in usefulness? See levels.py for the level definitions.

Usage:
    python run_pilot.py --smoke          # 1 clean task at L5, prints cost (~cents)
    python run_pilot.py                  # full pilot, stops at the budget cap

Optional in .env: LEVELS=L1,L2,L3,L4,L5 (which levels), REPEATS=1 (runs per case).
Finished runs are saved and reused, so a stopped run can simply be restarted.

Two ways to run, set PROVIDER in .env:
  PROVIDER=ollama     free, a local model on your Mac via Ollama (no API key; heavy on memory)
  PROVIDER=openai_compat  any OpenAI-compatible service, e.g. a free cloud tier (set BASE_URL, OPENAI_COMPAT_KEY)
  PROVIDER=anthropic  Claude via the API (needs ANTHROPIC_API_KEY)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

import anthropic  # noqa: E402
import openai  # noqa: E402
import agentdojo.agent_pipeline.llms.anthropic_llm as allm  # noqa: E402
from agentdojo.agent_pipeline import AgentPipeline, AnthropicLLM, InitQuery, OpenAILLM, SystemMessage, ToolsExecutionLoop  # noqa: E402
from agentdojo.agent_pipeline.agent_pipeline import load_system_message  # noqa: E402
from agentdojo.attacks.attack_registry import load_attack  # noqa: E402
from agentdojo.benchmark import run_task_with_injection_tasks, run_task_without_injection_tasks  # noqa: E402
from agentdojo.logging import OutputLogger  # noqa: E402
from agentdojo.task_suite.load_suites import get_suite  # noqa: E402

from agentdojo.models import MODEL_NAMES  # noqa: E402

from levels import GatedToolsExecutor  # noqa: E402

PROVIDER = os.getenv("PROVIDER", "ollama").strip().lower()
MODEL = os.getenv("MODEL", "qwen3:8b" if PROVIDER == "ollama" else "claude-haiku-5-5")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/v1")
BASE_URL = os.getenv("BASE_URL", "")
COMPAT_KEY = os.getenv("OPENAI_COMPAT_KEY", "")
BUDGET = float(os.getenv("BUDGET_USD", "5"))
PRICE_IN = float(os.getenv("PRICE_IN", "1.0"))  # USD per million input tokens: CHECK current pricing
PRICE_OUT = float(os.getenv("PRICE_OUT", "5.0"))  # USD per million output tokens: CHECK current pricing
_t = os.getenv("TEMPERATURE", "0").strip().lower()
TEMPERATURE = None if _t == "none" else float(_t)  # set TEMPERATURE=none if the model rejects it

BENCH_VERSION = "v1.2"
SUITE = os.getenv("SUITE", "banking").strip().lower()
NET_TAG = "-netgate" if os.getenv("NET_GATE", "0") == "1" else ""  # see levels.py
OUT = ("" if SUITE == "banking" else f"_{SUITE}") + NET_TAG.replace("-", "_")  # banking keeps the original names
ATTACK = "important_instructions"  # the standard attack from the AgentDojo paper
TASKS = {
    "banking": (["user_task_0", "user_task_2", "user_task_3", "user_task_4", "user_task_5"],
                ["injection_task_0", "injection_task_4", "injection_task_5", "injection_task_7"]),
    # one task per kind of action and per place the attacker can plant text; all 5 attacker goals
    "slack": (["user_task_1", "user_task_2", "user_task_4", "user_task_6", "user_task_8"],
              ["injection_task_1", "injection_task_2", "injection_task_3", "injection_task_4", "injection_task_5"]),
}
USER_TASKS, INJECTION_TASKS = TASKS[SUITE]
LEVELS = [x.strip() for x in os.getenv("LEVELS", "L1,L2,L3,L4,L5").split(",") if x.strip()]
if SUITE != "banking" and "L3" in LEVELS:
    LEVELS.remove("L3")  # L3's fixed rules were written for banking only
REPEATS = int(os.getenv("REPEATS", "1"))
LOGDIR = HERE / "runs"
MODEL_NAMES[MODEL] = "Claude" if PROVIDER == "anthropic" else "AI assistant"  # name the attack uses for the agent
MODEL_NAMES[MODEL.replace("/", "_")] = MODEL_NAMES[MODEL]

# ---- spend tracking: wrap AgentDojo's Anthropic call to read token usage ----
SPEND = {"in": 0, "out": 0, "calls": 0}
_orig = allm.chat_completion_request


class BudgetExceeded(Exception):
    pass


def usd() -> float:
    return SPEND["in"] / 1e6 * PRICE_IN + SPEND["out"] / 1e6 * PRICE_OUT


async def _tracked(*a, **kw):
    if usd() >= BUDGET:
        raise BudgetExceeded(f"budget ${BUDGET:.2f} reached")
    msg = await _orig(*a, **kw)
    SPEND["in"] += msg.usage.input_tokens
    SPEND["out"] += msg.usage.output_tokens
    SPEND["calls"] += 1
    return msg


allm.chat_completion_request = _tracked


class RetryingOpenAILLM(OpenAILLM):
    """Retries a model call when the provider rejects a malformed tool call.

    gpt-oss on Groq sometimes emits a tool name with formatting tokens attached
    (e.g. 'read_file<|channel|>commentary') or output Groq cannot parse; Groq answers 400
    and the run would crash.
    That is an output-format glitch, so the same call is simply retried.
    """

    RETRIES = 4
    glitches = 0

    def query(self, *args, **kwargs):
        for attempt in range(self.RETRIES):
            try:
                return super().query(*args, **kwargs)
            except openai.BadRequestError as e:
                msg = str(e).lower()
                glitch = "tool call validation failed" in msg or "parsing failed" in msg
                if not glitch or attempt == self.RETRIES - 1:
                    raise
                RetryingOpenAILLM.glitches += 1


def make_judge():
    """Reviewer used by L2 and L4: same provider and model as the agent unless REVIEWER_MODEL is set."""
    model = os.getenv("REVIEWER_MODEL", MODEL)
    if PROVIDER in ("ollama", "openai_compat"):
        oa = (openai.OpenAI(api_key="ollama", base_url=OLLAMA_URL) if PROVIDER == "ollama"
              else openai.OpenAI(api_key=COMPAT_KEY, base_url=BASE_URL, max_retries=8))

        def judge(prompt: str) -> str:
            r = oa.chat.completions.create(model=model, max_tokens=2000, temperature=0.01,
                                           messages=[{"role": "user", "content": prompt}])
            return r.choices[0].message.content or ""
    else:
        ac = anthropic.Anthropic()
        kw = {} if TEMPERATURE is None else {"temperature": TEMPERATURE}

        def judge(prompt: str) -> str:
            if usd() >= BUDGET:
                raise BudgetExceeded(f"budget ${BUDGET:.2f} reached")
            r = ac.messages.create(model=model, max_tokens=300, messages=[{"role": "user", "content": prompt}], **kw)
            SPEND["in"] += r.usage.input_tokens
            SPEND["out"] += r.usage.output_tokens
            SPEND["calls"] += 1
            return r.content[0].text
    return judge


def build_pipeline(level: str, repeat: int = 1) -> AgentPipeline:
    if PROVIDER == "ollama":
        client = openai.OpenAI(api_key="ollama", base_url=OLLAMA_URL)
        # AgentDojo drops temperature 0 for OpenAI-style APIs, so use a tiny value to stay near-deterministic
        llm = RetryingOpenAILLM(client, MODEL, temperature=TEMPERATURE or 0.01)
    elif PROVIDER == "openai_compat":
        client = openai.OpenAI(api_key=COMPAT_KEY, base_url=BASE_URL, max_retries=8)
        llm = RetryingOpenAILLM(client, MODEL, temperature=TEMPERATURE or 0.01)
    else:
        client = anthropic.AsyncAnthropic()
        llm = AnthropicLLM(client, MODEL, temperature=TEMPERATURE)
    pipe = AgentPipeline(
        [
            SystemMessage(load_system_message(None)),
            InitQuery(),
            llm,
            ToolsExecutionLoop([GatedToolsExecutor(level, make_judge() if level in ("L2", "L4") else None), llm]),
        ]
    )
    # repeat 1 keeps the original folder name so earlier runs are reused
    # no "/" in the name: AgentDojo saves logs under a "/"-free folder but looks for cached runs
    # under the raw name, so a "/" silently disables reuse of finished runs
    safe = MODEL.replace("/", "_")
    pipe.name = (f"{safe}-{level}" if repeat == 1 else f"{safe}-{level}-r{repeat}") + NET_TAG
    return pipe


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    if PROVIDER == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY missing: put it in .env, or set PROVIDER=ollama to run free on your Mac.")
    if PROVIDER == "openai_compat" and not (BASE_URL and COMPAT_KEY):
        sys.exit("Set BASE_URL and OPENAI_COMPAT_KEY in .env for PROVIDER=openai_compat.")
    if PROVIDER == "ollama":
        try:
            names = [m.id for m in openai.OpenAI(api_key="ollama", base_url=OLLAMA_URL).models.list().data]
        except Exception:
            sys.exit("Can't reach Ollama. Open the Ollama app (or run `ollama serve`) and try again.")
        if MODEL not in names:
            sys.exit(f"Model {MODEL} not pulled. Run: ollama pull {MODEL}   (you have: {', '.join(names) or 'none'})")

    suite = get_suite(BENCH_VERSION, SUITE)
    results = []
    failed: list[str] = []
    t0 = time.time()
    with OutputLogger(str(LOGDIR)):
        if args.smoke:
            pipe = build_pipeline("L5")
            ok, _ = run_task_without_injection_tasks(
                suite, pipe, suite.get_user_task_by_id(USER_TASKS[0]), LOGDIR, True, BENCH_VERSION
            )
            print(f"smoke: task solved={ok}  ({PROVIDER} {MODEL}; API spend est ${usd():.3f})")
            return
        try:
            for repeat in range(1, REPEATS + 1):
                for level in LEVELS:
                    pipe = build_pipeline(level, repeat)
                    attack = load_attack(ATTACK, suite, pipe)
                    for ut in USER_TASKS:
                        task = suite.get_user_task_by_id(ut)
                        try:
                            ok, _ = run_task_without_injection_tasks(suite, pipe, task, LOGDIR, False, BENCH_VERSION)
                            util, sec = run_task_with_injection_tasks(
                                suite, pipe, task, attack, LOGDIR, False, INJECTION_TASKS, BENCH_VERSION
                            )
                        except BudgetExceeded:
                            raise
                        except Exception as e:  # noqa: BLE001
                            failed.append(f"r{repeat} {level} {ut}: {type(e).__name__}: {str(e)[:120]}")
                            print(f"r{repeat} {level} {ut} FAILED, skipped (rerun later): {type(e).__name__}", flush=True)
                            continue
                        results.append({"level": level, "repeat": repeat, "user_task": ut, "injection_task": None,
                                        "utility": ok, "attack_success": None})
                        for (u, it), s in sec.items():
                            results.append({"level": level, "repeat": repeat, "user_task": u, "injection_task": it,
                                            "utility": util[(u, it)], "attack_success": s})
                        print(f"r{repeat} {level} {ut} done  est spend ${usd():.2f}", flush=True)
        except BudgetExceeded as e:
            print(f"STOPPED: {e}. Partial results saved.")
    out = {
        "provider": PROVIDER, "model": MODEL, "suite": SUITE, "attack": ATTACK, "benchmark_version": BENCH_VERSION,
        "levels": LEVELS, "repeats": REPEATS, "spend_estimate_usd": round(usd(), 3), "tokens": SPEND, "minutes": round((time.time() - t0) / 60, 1),
        "results": results,
    }
    out["failed_tasks"] = failed
    out["tool_call_glitch_retries"] = RetryingOpenAILLM.glitches
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / f"pilot_results{OUT}.json").write_text(json.dumps(out, indent=2))
    if failed:
        print(f"{len(failed)} task group(s) failed and were skipped. Run the same command again to fill them in:")
        print("\n".join("  " + f for f in failed))
    if RetryingOpenAILLM.glitches:
        print(f"Retried {RetryingOpenAILLM.glitches} malformed tool call(s) from the provider.")
    print(f"wrote results/pilot_results{OUT}.json  est spend ${usd():.2f}")


if __name__ == "__main__":
    main()
