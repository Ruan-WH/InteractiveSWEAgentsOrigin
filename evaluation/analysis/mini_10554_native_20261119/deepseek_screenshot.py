"""10554 ideal with the saved astropy DeepSeek text-protocol configuration."""

import argparse
from functools import partial
import json
from pathlib import Path

import httpx
import openai

from evaluation.analysis.mini_10554_ideal_20261119 import run as common
from evaluation.benchmarks.swe_bench.mini_swe.model import (
    BenchmarkModel,
    NativeBenchmarkModel,
)

CONFIG = "hidden_deepseek_flash"
REFERENCE = common.PROJECT / "evaluation/evaluation_outputs/mini_swe/deepseek-hidden-12907-smoke"


class RecordedTextModel(BenchmarkModel):
    _record_request = NativeBenchmarkModel._record_request

    def __init__(self, *, request_log_dir, **kwargs):
        super().__init__(**kwargs)
        self.request_log_dir = request_log_dir
        request_log_dir.mkdir(exist_ok=False)
        self.http_requests = self.http_responses = 0
        self.http_client = httpx.Client(event_hooks={
            "request": [self._record_request], "response": [self._record_response]
        })
        options = self.config.model_kwargs
        self.client = openai.OpenAI(
            api_key=options["api_key"], base_url=options["api_base"],
            timeout=options["timeout"], max_retries=2, http_client=self.http_client,
        )

    def _record_response(self, response):
        NativeBenchmarkModel._record_response(self, response)
        # Account failures must stop even though transient API retries match the reference.
        if response.status_code in (401, 402, 403) or (
            response.status_code == 429
            and any(word in response.text.lower() for word in ("quota", "balance", "credit", "billing"))
        ):
            self.client.max_retries = 0
            self.config.model_kwargs["num_retries"] = 0
            response.headers["x-should-retry"] = "false"

    def _query(self, messages, **kwargs):
        return super()._query(messages, client=self.client, **kwargs)

    def close(self):
        self.client.close()


def prepare(folder):
    common.prepare(folder, extra_sources=(Path(__file__),), solver_config=CONFIG, compare_old_model=False)
    manifest = json.loads((folder / "manifest.json").read_text())
    old = json.loads((REFERENCE / "manifest.json").read_text())
    assert manifest["model_settings"] == old["model_settings"]
    previous = folder.parent / "deepseek-10554-ideal-native-20261119-v1"
    for name in ("issue.txt", "ideal_question.txt", "ideal_answer.txt", "task.txt", "dependencies.txt"):
        assert (folder / name).read_bytes() == (previous / name).read_bytes()
    # Match the screenshot's actual fresh-shell interpreter; retain all isolation checks.
    state = json.loads((folder / "container.json").read_text())
    state["config"]["interpreter"] = ["timeout", "-s", "KILL", "120", "bash", "-c"]
    common.dump(folder / "container.json", state)
    manifest.update(
        protocol="text-mswea-bash-command", reference_run=str(REFERENCE),
        model_settings_exact_reference_match=True, api_retry_override=2,
        whole_request_attempts=1,
        differences=[
            "Different issue: 10554 uses its saved ideal QA with no further replies; astropy reference hidden uses its original issue",
            "Same model/endpoint/sampling/16384 output cap; thinking parameter omitted as in reference",
            "Same text Bash protocol, observation template and bash -c interpreter",
            "Same inner API retry setting 2; outer whole-request retries disabled to stop account/API errors safely",
            "Additional isolation verification and host-only checkpoint after 20 tools; no repair hints",
        ],
    )
    common.dump(folder / "manifest.json", manifest)


def run(folder):
    common.run(folder, model_class=partial(RecordedTextModel, request_log_dir=folder / "http"),
               stop_on_protocol_fault=True, solver_config=CONFIG, api_retries=2)
    pred = json.loads((folder / "predictions.json").read_text())
    pred["model_name_or_path"] = "mini-deepseek-screenshot-ideal-no-followup"
    common.dump(folder / "predictions.json", pred)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "run"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.stage == "prepare" else run)(args.output.resolve())
