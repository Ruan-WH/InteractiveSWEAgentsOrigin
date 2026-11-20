"""Use the upstream agent loop and add a bounded reporter action."""

import shlex

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import LimitsExceeded, Submitted

from evaluation.benchmarks.swe_bench.mini_swe.protocol import SUBMISSION_MARKER, submission_text


SYSTEM = """You are a software engineer fixing an issue in /testbed.
Explain your next action briefly, then provide exactly ONE command in this format:
```mswea_bash_command
your command
```
Use noninteractive bash commands to inspect files, edit source, and run tests.
Each command runs in a fresh shell in /testbed; cd and exports do not persist.
Make minimal production changes. Keep existing test expectations unchanged.
Do not browse the web or fetch upstream fixes. Do not commit your changes.
When finished, issue: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
The harness will extract the actual workspace diff, including new source files.
"""

INTERACTION = """
The report is summarized and the reporter has additional issue details.
As your first action, ask specific clarification questions. You may ask at most
three times, including later followups. To ask, use a standalone command:
```mswea_bash_command
ask_user 'Your specific question or batch of related questions'
```
This is a harness action, not a shell executable. Do not combine it with bash.
Use the answers to reproduce the reported behavior, edit the code, and verify it.
"""

NATIVE_SYSTEM = SYSTEM.replace(
    "Explain your next action briefly, then provide exactly ONE command in this format:\n"
    "```mswea_bash_command\nyour command\n```",
    "Explain your next action briefly, then use exactly ONE native bash tool call with a command argument.",
)


class AmbigAgent(DefaultAgent):
    def __init__(self, *args, reporter=None, tool_limit=100, **kwargs):
        model = args[0] if args else kwargs.get("model")
        if getattr(model, "stops_on_protocol_error", False):
            kwargs.setdefault("max_consecutive_format_errors", 1)
        super().__init__(*args, **kwargs)
        self.reporter = reporter
        self.tool_limit = tool_limit
        self.n_tool_calls = 0

    def serialize(self, *args):
        data = super().serialize(*args)
        data["info"]["tool_execution"] = {"limit": self.tool_limit,
                                          "executed": self.n_tool_calls, "mode": "sequential"}
        return data

    def query(self) -> dict:
        message = super().query()
        print(
            f"[{getattr(self, 'instance_id', 'mini')}] step {self.n_calls}/{self.config.step_limit}",
            flush=True,
        )
        return message

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])
        if not actions and (submission := submission_text(message.get("content"))) is not None:
            raise Submitted({"role": "exit", "content": submission,
                             "extra": {"exit_status": "Submitted", "submission": submission,
                                       "completion_source": "explicit_native_final_marker"}})
        outputs = []
        finished = None
        for action in actions:
            if 0 < self.tool_limit <= self.n_tool_calls:
                finished = LimitsExceeded({"role": "exit", "content": "ToolLimitExceeded",
                                           "extra": {"exit_status": "ToolLimitExceeded", "submission": ""}})
                break
            self.n_tool_calls += 1
            command = action.get("command", "").strip()
            if command == "ask_user" or command.startswith(("ask_user ", "ask_user\n")):
                try:
                    words = shlex.split(command)
                except ValueError:
                    words = []
                if len(words) != 2:
                    output = (
                        "Use exactly: ask_user 'your question'. No shell operators."
                    )
                elif self.reporter is None:
                    output = "No reporter is available in this setting. Continue fixing the issue."
                else:
                    output = self.reporter.reply(words[1])
                outputs.append(
                    {"output": output, "returncode": 0, "exception_info": ""}
                )
            else:
                try:
                    outputs.append(self.env.execute(action))
                except Submitted as error:
                    outputs.append(error.messages[0].get("extra", {}).get("command_output", {
                        "output": SUBMISSION_MARKER + "\n" + error.messages[0].get("content", ""),
                        "returncode": 0, "exception_info": "",
                    }))
                    finished = error
                    break
        observations = self.add_messages(
            *self.model.format_observation_messages(
                message, outputs, self.get_template_vars()
            )
        )
        if finished is not None:
            raise finished
        return observations
