"""Translate existing TOML model groups without serializing credentials."""

import copy
import json
import os
from pathlib import Path

import httpx
import openai
from minisweagent.models.litellm_model import LitellmModel
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
from minisweagent.exceptions import FormatError

from evaluation.benchmarks.swe_bench.mini_swe.protocol import submission_text


def model_options(settings: dict) -> dict:
    options = {
        key: settings[key]
        for key in ("temperature", "top_p", "extra_body")
        if key in settings
    }
    options.update(
        api_base=settings.get("base_url"),
        api_key=os.path.expandvars(settings["api_key"]),
        timeout=settings.get("timeout", 180),
        num_retries=2,
    )
    if "max_output_tokens" in settings:
        options["max_tokens"] = settings["max_output_tokens"]
    return options


class BenchmarkModel(LitellmTextbasedModel):
    def serialize(self) -> dict:
        data = copy.deepcopy(super().serialize())
        options = data["info"]["config"]["model"]["model_kwargs"]
        options.pop("api_key", None)
        return data


class NativeBenchmarkModel(LitellmModel):
    """Upstream native Bash tool interface, with credential-free wire capture."""

    stops_on_protocol_error = True

    def __init__(self, *, request_log_dir: Path, tool_choice="required", **kwargs):
        super().__init__(**kwargs)
        self.config.model_kwargs.update(
            tool_choice=tool_choice, parallel_tool_calls=False
        )
        self.request_log_dir = request_log_dir
        self.request_log_dir.mkdir(parents=True, exist_ok=False)
        self.http_requests = 0
        self.http_responses = 0
        self.http_client = httpx.Client(
            event_hooks={
                "request": [self._record_request],
                "response": [self._record_response],
            }
        )
        options = self.config.model_kwargs
        self.client = openai.OpenAI(
            api_key=options["api_key"],
            base_url=options["api_base"],
            timeout=options["timeout"],
            max_retries=options.get("num_retries", 0),
            http_client=self.http_client,
        )

    def _record_request(self, request):
        self.http_requests += 1
        body = json.loads(request.content)
        record = {"method": request.method, "url": str(request.url), "body": body}
        (self.request_log_dir / f"{self.http_requests:03}.request.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )

    def _record_response(self, response):
        response.read()
        self.http_responses += 1
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text}
        record = {"status_code": response.status_code, "body": body}
        (self.request_log_dir / f"{self.http_responses:03}.response.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )
        if response.status_code in (401, 402, 403) or (
            response.status_code == 429
            and any(word in response.text.lower() for word in ("quota", "balance", "credit", "billing"))
        ):
            self.client.max_retries = 0
            self.config.model_kwargs["num_retries"] = 0
            response.headers["x-should-retry"] = "false"

    def _query(self, messages, **kwargs):
        return super()._query(messages, client=self.client, **kwargs)

    def _parse_actions(self, response):
        message = response.choices[0].message
        if not message.tool_calls and submission_text(message.content) is not None:
            return []
        actions = super()._parse_actions(response)
        ids = [action.get("tool_call_id") for action in actions]
        error = None
        if any(not isinstance(a["command"], str) or not a["command"].strip() for a in actions):
            error = "Expected native Bash calls with a nonempty string command"
        elif any(not isinstance(call_id, str) or not call_id for call_id in ids) or len(set(ids)) != len(ids):
            error = "Expected unique nonempty native tool call IDs"
        if error:
            # Upstream persists the billed response/usage on FormatError. The agent
            # stops after one such error instead of retrying an invalid contract.
            raise FormatError({"role": "user", "content": error,
                               "extra": {"interrupt_type": "FormatError"}})
        return actions

    def serialize(self):
        data = copy.deepcopy(super().serialize())
        data["info"]["config"]["model"]["model_kwargs"].pop("api_key", None)
        return data

    def close(self):
        self.client.close()


def make_model(settings: dict):
    return BenchmarkModel(
        model_name=settings["model"],
        model_kwargs=model_options(settings),
        cost_tracking="ignore_errors",
        observation_template=OBSERVATION,
    )


OBSERVATION = """{% if output.exception_info %}<exception>{{output.exception_info}}</exception>{% endif %}
<returncode>{{output.returncode}}</returncode>
<output>{{output.output[:8000]}}{% if output.output|length > 16000 %}
... output truncated ...
{{output.output[-8000:]}}{% elif output.output|length > 8000 %}{{output.output[8000:]}}{% endif %}</output>"""
