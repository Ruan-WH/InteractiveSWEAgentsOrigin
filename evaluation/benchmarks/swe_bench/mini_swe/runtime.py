"""Fresh Docker commands against the image's installed /testbed source tree."""

import shlex
import subprocess
from pathlib import Path

from minisweagent.environments.docker import DockerEnvironment
from minisweagent.exceptions import Submitted

from evaluation.benchmarks.swe_bench.mini_swe.protocol import submission_text


def image_candidates(instance_id: str) -> list[str]:
    return [
        f"sweb.eval.x86_64.{instance_id}:latest".lower(),
        (
            "docker.io/xingyaoww/sweb.eval.x86_64." + instance_id.replace("__", "_s_")
        ).lower()
        + ":latest",
        (
            "docker.io/swebench/sweb.eval.x86_64." + instance_id.replace("__", "_1776_")
        ).lower()
        + ":latest",
    ]


def find_image(instance_id: str, template: str | None = None) -> str:
    candidates = (
        [template.format(instance_id=instance_id)]
        if template
        else image_candidates(instance_id)
    )
    for image in candidates:
        if (
            subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, timeout=30
            ).returncode
            == 0
        ):
            return image
    raise RuntimeError(f"No local image for {instance_id}. Pull one of: {candidates}")


class BenchmarkEnvironment(DockerEnvironment):
    def _check_finished(self, output):
        submission = submission_text(output.get("output"))
        if output.get("returncode") == 0 and submission is not None:
            raise Submitted({"role": "exit", "content": submission,
                             "extra": {"exit_status": "Submitted", "submission": submission,
                                       "command_output": output}})

    def cleanup(self):
        if self.container_id:
            subprocess.run(
                ["docker", "rm", "-f", self.container_id],
                capture_output=True,
                timeout=30,
                check=True,
            )
            self.container_id = None

    def execute(self, *args, **kwargs):
        output = super().execute(*args, **kwargs)
        if output.get("returncode") == -1:
            raise RuntimeError(
                "Docker command failed: " + output.get("exception_info", "")
            )
        return output

    def checked(self, command: str, timeout=300) -> str:
        output = self.execute({"command": command}, timeout=timeout)
        if output["returncode"]:
            raise RuntimeError(
                f"Environment command failed ({output['returncode']}): {output['output']}"
            )
        return output["output"]


def start_environment(image: str, command_timeout: int, network: str):
    env = BenchmarkEnvironment(
        image=image,
        cwd="/testbed",
        timeout=command_timeout + 15,
        interpreter=["timeout", "-s", "KILL", str(command_timeout), "bash", "-c"],
        run_args=[
            "--rm",
            "--pull=never",
            "--network=" + network,
            "--memory=6g",
            "--memory-swap=8g",
        ],
        env={
            "PAGER": "cat",
            "PIP_PROGRESS_BAR": "off",
            "BASH_ENV": "/tmp/ambig-mini-env.sh",
        },
    )
    return env


def initialize(env, instance: dict) -> str:
    # The image's editable Python installation already points to /testbed.
    env.checked("""cat > /tmp/ambig-mini-env.sh <<'EOF'
for profile in /opt/miniconda3/etc/profile.d/conda.sh /opt/conda/etc/profile.d/conda.sh; do
  if [ -f "$profile" ]; then
    . "$profile"
    conda activate testbed || exit 1
    break
  fi
done
EOF
""")
    startup = env.checked(
        "git reset --hard "
        + shlex.quote(instance["base_commit"])
        + ' && git clean -fd && for remote in $(git remote); do git remote remove "$remote"; done'
        + " && python -c 'import sys; print(sys.executable)' && git rev-parse HEAD"
    )
    # Reuse only the standalone Git cleanup, never OpenHands runtime code.
    source = Path(__file__).resolve().parents[3] / "utils/reference_history.py"
    subprocess.run(
        ["docker", "cp", str(source), env.container_id + ":/tmp/reference_history.py"],
        capture_output=True,
        timeout=30,
        check=True,
    )
    env.checked(
        "python -c "
        + shlex.quote(
            'import sys; sys.path.insert(0, "/tmp"); from reference_history import clean_repository; '
            'print(clean_repository("/testbed", ' + repr(instance["base_commit"]) + "))"
        )
    )
    env.checked("rm -rf /swe_util/eval_data /root/.cache/pip")
    return startup


def extract_patch(env, base_commit: str) -> str:
    # A separate index includes untracked source without depending on the agent's staging or commits.
    return env.checked(
        "GIT_INDEX_FILE=/tmp/ambig-mini-patch.index; export GIT_INDEX_FILE; "
        "git read-tree "
        + shlex.quote(base_commit)
        + " && git add -A && git -c core.pager=cat diff --cached --binary "
        + shlex.quote(base_commit)
        + " --"
    )
