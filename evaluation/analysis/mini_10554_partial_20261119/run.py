"""Independent partial arms using the original pre-repair clarification module."""

import argparse
from functools import partial
import json
from pathlib import Path
import time
import tomllib

from evaluation.analysis.mini_10554_ideal_20261119 import run as common

CONFIG = "hidden_deepseek_flash"
MODULES = ["simple_followup", "simple_clarification", "answer_check", "controlled_proxy"]


def prepare(folder, followup):
    sources = [common.PROJECT / f"evaluation/utils/{name}.py" for name in MODULES]
    old = json.loads((common.OLD / "manifest.json").read_text())
    for source in sources:
        assert common.sha(source) == old["source_sha256"][str(source.relative_to(common.PROJECT))]
    common.prepare(folder, extra_sources=(Path(__file__), *sources),
                   solver_config=CONFIG, compare_old_model=False)
    fixture_path = common.OLD / "frozen/fixture.json"
    fixture = json.loads(fixture_path.read_text())
    exchange = {"question": fixture["initial_responses"]["question"],
                "answer": fixture["initial_responses"]["partial"]["answer"]}
    for name in ("ideal_question.txt", "ideal_answer.txt"):
        (folder / name).unlink()
    (folder / "initial_question.txt").write_text(exchange["question"])
    (folder / "partial_answer.txt").write_text(exchange["answer"])
    common.dump(folder / "public_input.json", {"issue": fixture["public_task"]["summary"], **exchange})
    # No live calls during preparation. The final handoff is frozen after clarification.
    (folder / "task.txt").unlink()
    manifest = json.loads((folder / "manifest.json").read_text())
    reference = folder.parent / "deepseek-10554-ideal-thinking-native-20261119-v2"
    ref = json.loads((reference / "manifest.json").read_text())
    assert manifest["model_settings"] == ref["model_settings"]
    assert manifest["image_id"] == ref["image_id"]
    assert (folder / "dependencies.txt").read_bytes() == (reference / "dependencies.txt").read_bytes()
    manifest.update(
        condition="partial_followup" if followup else "partial_no_followup",
        protocol="native-structured-bash-original-simple-clarification-v1",
        tool_choice="auto", api_retry_override=2, whole_request_attempts=1,
        proxy_enabled=followup, checker_enabled=followup, followups_enabled=followup,
        max_followups=2 if followup else 0,
        clarification_actor="pre-repair answer-check module, not autonomous repair-agent tools",
        checker_config_name="hidden_deepseek_flash", proxy_config_name="hidden_qwen_coder",
        comparison_run=str(reference),
        input_sha256={p.name: common.sha(p) for p in folder.glob("*.txt")},
        task_content="verbatim issue and partial Q/A; original checked_solving_instruction public handoff",
        differences=[
            "Same successful DeepSeek solver/native auto/default thinking/output16384/temp0/top_p1/100 calls/pipefail",
            "Partial initial answer instead of ideal; both arms use the same original public handoff wrapper",
            "Followup arm alone uses unchanged simple-v1 checker and controlled rule-template proxy, max2 questions",
            "Original proxy remains Qwen3-coder-flash; repair and checker remain DeepSeek",
            "No whole-case retries, no transport retries for clarification, account errors stop",
        ],
        analysis_criteria_before_live=[
            "Does a question specifically request missing intervening operation and/or failure sequence?",
            "Does an answer supply exact derived call, not merely repeat public-summary timing?",
            "Trace received facts to solver reproduction, production edit and local verification",
            "Independent official evaluation after immutable raw patch; no result returned to solver",
        ],
    )
    settings = tomllib.loads((common.PROJECT / "config.toml").read_text())["llm"]
    manifest["clarification_settings"] = {
        name: {k: v for k, v in settings[name].items() if "key" not in k}
        for name in ("hidden_deepseek_flash", "hidden_qwen_coder")
    }
    common.dump(folder / "manifest.json", manifest)


def clarify(folder):
    from openai import OpenAI
    import httpx
    from evaluation.utils.answer_check import checked_solving_instruction
    from evaluation.utils.controlled_proxy import ControlledProxy
    from evaluation.utils.simple_clarification import SimpleJSONChecker, run_simple_clarification

    manifest = json.loads((folder / "manifest.json").read_text())
    assert not (folder / "clarification_started.json").exists(), "Preserve prior attempt"
    assert all(common.sha(common.PROJECT / p) == digest for p, digest in manifest["source_sha256"].items())
    common.dump(folder / "clarification_started.json", {"at": common.now()})
    start = time.monotonic()
    fixture_path = common.OLD / "frozen/fixture.json"
    assert common.sha(fixture_path) == manifest["old_fixture_sha256"]
    fixture = json.loads(fixture_path.read_text())
    public = json.loads((folder / "public_input.json").read_text())
    exchange = {k: public[k] for k in ("question", "answer")}
    settings = tomllib.loads((common.PROJECT / "config.toml").read_text())["llm"]
    clients = []

    def client_for(name, role):
        cfg = settings[name]
        assert {k: v for k, v in cfg.items() if "key" not in k} == manifest["clarification_settings"][name]
        wire = folder / "clarification_http" / role
        wire.mkdir(parents=True, exist_ok=False)
        counts = {"request": 0, "response": 0}

        def request(req):
            counts["request"] += 1
            common.dump(wire / f"{counts['request']:03}.request.json",
                        {"method": req.method, "url": str(req.url), "body": json.loads(req.content)})

        def response(res):
            res.read()
            counts["response"] += 1
            try:
                body = res.json()
            except ValueError:
                body = {"raw_text": res.text}
            common.dump(wire / f"{counts['response']:03}.response.json", {"status_code": res.status_code, "body": body})

        http = httpx.Client(event_hooks={"request": [request], "response": [response]})
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=120,
                        max_retries=0, http_client=http)
        clients.append(client)
        return client

    if manifest["followups_enabled"]:
        proxy = ControlledProxy(fixture, "partial", client_for("hidden_qwen_coder", "proxy"),
                                settings["hidden_qwen_coder"]["model"].removeprefix("openai/"),
                                folder / "interactions.json", 0, 1)
        assert proxy.initial_exchange() == exchange
        checker = SimpleJSONChecker(client_for(CONFIG, "checker"), settings[CONFIG]["model"])
        record = run_simple_clarification(checker, public["issue"], [exchange], proxy,
                                          folder / "answer_check.json", max_followups=2)
    else:
        record = {
            "protocol": "fixed-initial-only-v1", "status": "not_run", "task_summary": public["issue"],
            "exchanges": [exchange], "checks": [], "max_followups": 0, "followups_used": 0,
            "stop_reason": "initial_only_control", "unresolved_information": "",
            "unavailable_information": [], "error": None,
        }
        common.dump(folder / "answer_check.json", record)
    for client in clients:
        client.close()
    common.dump(folder / "clarification_result.json", {"elapsed_seconds": time.monotonic() - start,
                "status": record["status"], "followups_used": record["followups_used"]})
    if record["status"] == "failed":
        raise RuntimeError("Clarification failed; no repair run: " + str(record["error"]))
    task = checked_solving_instruction(public["issue"], record)
    (folder / "task.txt").write_text(task)
    common.dump(folder / "public_handoff.json", {"issue": public["issue"], "exchanges": record["exchanges"]})
    manifest["input_sha256"]["task.txt"] = common.sha(folder / "task.txt")
    manifest["handoff_frozen_at"] = common.now()
    common.dump(folder / "manifest.json", manifest)
    print("HANDOFF FROZEN " + str(folder), flush=True)


def run(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import NATIVE_SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import NativeBenchmarkModel

    manifest = json.loads((folder / "manifest.json").read_text())
    assert common.sha(folder / "task.txt") == manifest["input_sha256"]["task.txt"]
    common.run(folder, model_class=partial(NativeBenchmarkModel, request_log_dir=folder / "http", tool_choice="auto"),
               system_template=NATIVE_SYSTEM, stop_on_protocol_fault=True, solver_config=CONFIG, api_retries=2)
    pred = json.loads((folder / "predictions.json").read_text())
    pred["model_name_or_path"] = "mini-deepseek-thinking-native-" + manifest["condition"]
    common.dump(folder / "predictions.json", pred)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "clarify", "run"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--followup", action="store_true")
    args = parser.parse_args()
    folder = args.output.resolve()
    if args.stage == "prepare":
        prepare(folder, args.followup)
    else:
        (clarify if args.stage == "clarify" else run)(folder)
