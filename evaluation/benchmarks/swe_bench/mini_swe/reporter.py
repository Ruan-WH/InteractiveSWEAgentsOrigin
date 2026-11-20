"""Keep private issue data on the host; expose only reporter answers."""

import json
from pathlib import Path
from types import SimpleNamespace

import openai

from evaluation.benchmarks.swe_bench.mini_swe.model import model_options


class Reporter:
    def __init__(
        self,
        instance: dict,
        settings: dict,
        log_path: Path,
        fixture=None,
        condition=None,
    ):
        options = model_options(settings)
        self.client = openai.OpenAI(
            api_key=options["api_key"],
            base_url=options["api_base"],
            timeout=options["timeout"],
            max_retries=2,
        )
        self.settings = settings
        self.log_path = log_path
        self.interactions = []
        self.controlled = None
        self.initial = None
        if fixture is not None:
            from evaluation.utils.controlled_proxy import ControlledProxy

            if (
                fixture["instance_id"] != instance["instance_id"]
                or fixture["base_commit"] != instance["base_commit"]
                or fixture["public_task"]["summary"] != instance["problem_statement"]
            ):
                raise ValueError(
                    "Fixture must match instance ID, base commit, and public summary"
                )
            self.controlled = ControlledProxy(
                fixture,
                condition,
                self.client,
                settings["model"].removeprefix("openai/"),
                log_path,
                settings.get("temperature", 0),
                settings.get("top_p", 1),
            )
            self.initial = self.controlled.initial_exchange()
            self.interactions = self.controlled.interactions
        self.messages = [
            {
                "role": "system",
                "content": f"""You are a GitHub user reporting an issue.
Issue: {instance["original_issue"]}
Hints: {instance.get("hints_text", "")}
Files relative to your current directory: {instance.get("files", "")}
Respond concisely to questions from a coder who only has a summary of your issue.
Give information directly related to the source issue and hints. Stay in character.
If unrelated or unsure, say "I don't have that information".
Do not invent facts. You have at most three exchanges.""",
            }
        ]

    def reply(self, question: str) -> str:
        if len(self.interactions) >= 3:
            return "Please continue working on the task. Do NOT ask for more help."
        if self.controlled:
            return self.controlled.generate_reply(SimpleNamespace(content=question))
        self.messages.append({"role": "user", "content": question})
        response = self.client.chat.completions.create(
            model=self.settings["model"].removeprefix("openai/"),
            messages=self.messages,
            temperature=self.settings.get("temperature", 0),
            top_p=self.settings.get("top_p", 1),
            max_tokens=self.settings.get("max_output_tokens", 8192),
            extra_body=self.settings.get("extra_body"),
        )
        answer = response.choices[0].message.content
        if not answer or not answer.strip():
            raise ValueError("Reporter returned an empty answer")
        self.messages.append({"role": "assistant", "content": answer})
        self.interactions.append(
            {
                "turn": len(self.interactions) + 1,
                "question": question,
                "answer": answer,
                "model": response.model,
                "usage": response.usage.model_dump() if response.usage else None,
            }
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            json.dumps(self.interactions, ensure_ascii=False, indent=2)
        )
        return answer
