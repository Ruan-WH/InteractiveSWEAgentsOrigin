"""One sealed 10554 ideal/no-followup comparison; prepare makes no model calls."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from zoneinfo import ZoneInfo

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
os.environ.setdefault("MSWEA_GLOBAL_CONFIG_DIR", "/tmp/ambig-mini-config")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "1"

PROJECT = Path(__file__).resolve().parents[3]
OLD = PROJECT / "evaluation/evaluation_outputs/model-swap-10554-offline-20261119"
IID = "django__django-10554"
CONFIG = "hidden_qwen38_flash"
IMAGE = "xingyaoww/sweb.eval.x86_64.django_s_django-10554:latest"


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def shell(env, command):
    result = env.execute({"command": command}, timeout=300)
    if result["returncode"]:
        raise RuntimeError(str(result))
    return result["output"]


def python(env, code):
    return shell(env, "python -c " + shlex.quote(code))


def restore_env(folder):
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import BenchmarkEnvironment
    from minisweagent.environments.docker import DockerEnvironmentConfig

    state = json.loads((folder / "container.json").read_text())
    env = BenchmarkEnvironment.__new__(BenchmarkEnvironment)
    env.config = DockerEnvironmentConfig(**state["config"])
    env.container_id = state["container_id"]
    import logging

    env.logger = logging.getLogger("10554.environment")
    return env


def prepare(folder, extra_sources=(), solver_config=CONFIG, compare_old_model=True, settings_override=None):
    import docker
    import minisweagent
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import (
        initialize,
        start_environment,
    )

    folder.mkdir(parents=True, exist_ok=False)
    old = json.loads((OLD / "manifest.json").read_text())
    fixture_path = OLD / "frozen/fixture.json"
    assert sha(fixture_path) == old["fixture_sha256"]
    assert (
        sha(PROJECT / "evaluation/utils/reference_history.py")
        == old["source_sha256"]["evaluation/utils/reference_history.py"]
    )
    fixture = json.loads(fixture_path.read_text())
    settings = tomllib.loads((PROJECT / "config.toml").read_text())["llm"][
        solver_config
    ]
    settings.update(settings_override or {})
    if compare_old_model:
        assert all(
            settings[key] == value for key, value in old["solver_config"].items()
        )
    prior = json.loads(Path(old["arms"]["ideal_no_followup"]["raw_output"]).read_text())
    if compare_old_model:
        assert settings["base_url"] == prior["metadata"]["llm_config"]["base_url"]
    exchange = {
        "question": fixture["initial_responses"]["question"],
        "answer": fixture["initial_responses"]["ideal"]["answer"],
    }
    assert prior["test_result"]["answer_check"]["exchanges"] == [exchange]
    issue = fixture["public_task"]["summary"]
    task = (
        issue
        + "\n\nRecorded question:\n"
        + exchange["question"]
        + "\n\nRecorded answer:\n"
        + exchange["answer"]
    )
    (folder / "issue.txt").write_text(issue)
    (folder / "ideal_question.txt").write_text(exchange["question"])
    (folder / "ideal_answer.txt").write_text(exchange["answer"])
    (folder / "task.txt").write_text(task)
    dump(folder / "public_input.json", {"issue": issue, **exchange})
    dump(
        folder / "instance.json",
        {key: fixture[key] for key in ("instance_id", "base_commit")},
    )
    client = docker.from_env()
    image = client.images.get(IMAGE)
    env = start_environment(image.id, 120, "none")
    # Prevent the upstream destructor removing the intentionally prepared container.
    env.cleanup = lambda: None
    dump(
        folder / "container.json",
        {"container_id": env.container_id, "config": env.config.model_dump()},
    )
    startup = initialize(env, {"base_commit": fixture["base_commit"]})
    env.config.interpreter = [
        "timeout",
        "-s",
        "KILL",
        "120",
        "bash",
        "-o",
        "pipefail",
        "-c",
    ]
    # Hide cached setup artifacts; never inject source answers or fixture files.
    shell(
        env,
        "rm -f /root/setup_repo.sh /root/setup_env.sh /root/.bash_history; rm -rf /root/.cache/pip",
    )
    alignment = json.loads(
        python(
            env,
            """import django,json,pathlib,sys,subprocess
p=pathlib.Path(django.__file__).resolve()
assert str(p)=="/testbed/django/__init__.py"
original=p.read_bytes()
try:
 p.write_bytes(original+b"\\n_MINI_IMPORT_ALIGNMENT_PROBE = 'visible'\\n")
 result=subprocess.check_output([sys.executable,'-B','-c',"import django; print(django._MINI_IMPORT_ALIGNMENT_PROBE)"],cwd='/testbed').decode().strip()
 assert result=='visible'
finally:
 p.write_bytes(original)
print(json.dumps({'cwd':str(pathlib.Path.cwd()),'django_import':str(p),'python':sys.executable,'python_version':sys.version,'edit_visible':True,'restored_exact_bytes':p.read_bytes()==original}))""",
        )
    )
    baseline = fixture["base_commit"]
    history = json.loads(
        python(
            env,
            """import subprocess,json,pathlib
def git(*args): return subprocess.check_output(['git']+list(args)).decode().strip()
base="""
            + repr(baseline)
            + """
objects=git('cat-file','--batch-all-objects','--batch-check=%(objectname) %(objecttype)').splitlines()
reachable=set(git('rev-list','--objects','--no-object-names','HEAD').splitlines())
all_objects={line.split()[0] for line in objects}
commits=[line.split()[0] for line in objects if line.endswith(' commit')]
assert commits==[base] and all_objects==reachable
assert not git('remote') and not git('status','--porcelain')
refs=git('show-ref').splitlines()
assert refs==[base+' refs/heads/baseline']
logs={str(p):p.read_text() for p in pathlib.Path('.git/logs').rglob('*') if p.is_file()} if pathlib.Path('.git/logs').exists() else {}
for text in logs.values():
 for line in text.splitlines():
  assert set(line.split()[:2]) <= {base,'0'*40}
assert not pathlib.Path('.git/objects/info/alternates').exists()
assert not git('fsck','--full','--no-reflogs','--unreachable')
remote=subprocess.run(['git','ls-remote','https://github.com/django/django.git'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
assert remote.returncode!=0
print(json.dumps({'base_commit':base,'refs':refs,'reflogs':logs,'commit_objects':commits,'all_objects':len(all_objects),'unreachable_objects':len(all_objects-reachable),'alternates':False,'remotes':[],'protocol_allow':git('config','protocol.allow'),'remote_access_exit':remote.returncode,'remote_access_error':remote.stderr.decode(),'tracked_diff':git('diff',base),'status':git('status','--porcelain')}))""",
        )
    )
    dependencies = shell(env, "python -m pip freeze")
    (folder / "dependencies.txt").write_text(dependencies)
    repositories = shell(
        env,
        "find / -path /proc -prune -o -path /sys -prune -o -path /dev -prune -o -name .git -print -prune 2>/dev/null",
    ).splitlines()
    assert repositories == ["/testbed/.git"]
    isolation = json.loads(
        python(
            env,
            """import socket,json,pathlib
s=socket.socket();s.settimeout(2)
try:
 s.connect(('1.1.1.1',443)); blocked=False
except OSError: blocked=True
finally: s.close()
assert blocked
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/swe_util/eval_data').exists()
print(json.dumps({'external_tcp_blocked':blocked,'docker_socket':False,'instance_metadata_present':False}))""",
        )
    )
    container = client.containers.get(env.container_id)
    attrs = container.attrs
    assert attrs["HostConfig"]["NetworkMode"] == "none" and not attrs["Mounts"]
    assert not attrs["HostConfig"]["Privileged"] and not attrs["HostConfig"]["Binds"]
    failure = env.execute(
        {
            "command": "(printf 'intentional transport failure probe\\n' >&2; exit 7) | cat"
        }
    )
    assert failure["returncode"] == 7
    dump(
        folder / "preflight.json",
        {
            "checked_at": now(),
            "model_calls": 0,
            "startup": startup,
            "alignment": alignment,
            "history": history,
            "repositories": repositories,
            "isolation": isolation,
            "mounts": attrs["Mounts"],
            "network": attrs["HostConfig"]["NetworkMode"],
            "privileged": attrs["HostConfig"]["Privileged"],
            "pipeline_failure_probe": failure,
            "passed": True,
        },
    )
    dump(
        folder / "container.json",
        {"container_id": env.container_id, "config": env.config.model_dump()},
    )
    sources = [
        Path(__file__),
        *sorted((PROJECT / "evaluation/benchmarks/swe_bench/mini_swe").glob("*.py")),
        PROJECT / "evaluation/utils/reference_history.py",
        *extra_sources,
    ]
    frozen = folder / "frozen_source"
    frozen.mkdir()
    hashes = {}
    for source in sources:
        target = frozen / source.relative_to(PROJECT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[str(source.relative_to(PROJECT))] = sha(source)
    dump(
        folder / "manifest.json",
        {
            "created_at": now(),
            "condition": "ideal_no_followup",
            "solver_config_name": solver_config,
            "instance_id": IID,
            "model_settings": {k: v for k, v in settings.items() if "key" not in k},
            "api_retry_override": 0,
            "whole_request_attempts": 1,
            "max_model_calls": 100,
            "max_tool_calls": 100,
            "mini_version": minisweagent.__version__,
            "mini_git_commit": subprocess.check_output(
                ["git", "-C", "/home/rwh/mini-SWE-agent", "rev-parse", "HEAD"],
                text=True,
            ).strip(),
            "image_name": IMAGE,
            "image_id": image.id,
            "base_commit": baseline,
            "source_sha256": hashes,
            "input_sha256": {p.name: sha(p) for p in folder.glob("*.txt")},
            "old_manifest": str(OLD / "manifest.json"),
            "old_fixture_sha256": sha(fixture_path),
            "proxy_enabled": False,
            "checker_enabled": False,
            "followups_enabled": False,
            "extra_test_context": False,
            "task_content": "verbatim old issue summary, initial question and ideal answer; plain wrapping only",
            "differences": [
                "upstream mini text-based command protocol instead of OpenHands native tools",
                "single /testbed working/import source tree instead of copied /workspace tree",
                "bash pipefail and independent command processes",
                "no API retries; same sampling and thinking settings",
            ],
        },
    )
    client.close()
    print("PREPARED " + str(folder), flush=True)


def run(
    folder,
    model_class=None,
    system_template=None,
    stop_on_protocol_fault=False,
    solver_config=CONFIG,
    api_retries=0,
    settings_override=None,
):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import AmbigAgent, SYSTEM
    from evaluation.benchmarks.swe_bench.mini_swe.model import (
        BenchmarkModel,
        model_options,
        OBSERVATION,
    )
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import extract_patch

    manifest = json.loads((folder / "manifest.json").read_text())
    assert json.loads((folder / "preflight.json").read_text())["passed"]
    assert not (folder / "run_started.json").exists(), (
        "Do not rerun an existing model attempt"
    )
    assert all(
        sha(PROJECT / p) == digest for p, digest in manifest["source_sha256"].items()
    )
    settings = tomllib.loads((PROJECT / "config.toml").read_text())["llm"][
        solver_config
    ]
    settings.update(settings_override or {})
    assert {k: v for k, v in settings.items() if "key" not in k} == manifest[
        "model_settings"
    ]
    options = model_options(settings)
    options["num_retries"] = api_retries
    # Strip unsupported response fields only as upstream's text interface normally does.
    model = (model_class or BenchmarkModel)(
        model_name=settings["model"],
        model_kwargs=options,
        cost_tracking="ignore_errors",
        observation_template=OBSERVATION,
    )
    env = restore_env(folder)
    events = []
    original = env.execute
    start = time.monotonic()
    dump(folder / "run_started.json", {"started_at": now()})

    def execute(action, *args, **kwargs):
        if len(events) >= 100:
            raise RuntimeError("Tool budget exhausted")
        if len(events) == 20 and not (folder / "checkpoint20_review.json").exists():
            dump(folder / "checkpoint20_waiting.json", {"at": now(), "tool_calls": 20})
            while not (folder / "checkpoint20_review.json").exists():
                time.sleep(1)
            review = json.loads((folder / "checkpoint20_review.json").read_text())
            if not review["continue"]:
                raise RuntimeError(
                    "Stopped after 20-tool fault review: " + review["reason"]
                )
        t = time.monotonic()
        event = {
            "tool_call": len(events) + 1,
            "elapsed_start_seconds": t - start,
            "command": action.get("command", ""),
        }
        try:
            result = original(action, *args, **kwargs)
            event["result"] = result
            if (
                stop_on_protocol_fault
                and result["returncode"] == 2
                and (
                    "</parameter>" in action.get("command", "")
                    or "mswea_bash_command" in action.get("command", "")
                )
                and "syntax error" in result["output"]
            ):
                raise RuntimeError(
                    "Protocol residue caused a shell syntax error; stopping without a model retry"
                )
            return result
        except BaseException as exc:
            event["exception_type"] = type(exc).__name__
            event["exception"] = str(exc)
            raise
        finally:
            event["duration_seconds"] = time.monotonic() - t
            event["tracked_changes"] = shell_plain(
                "git diff --name-only " + manifest["base_commit"]
            )
            events.append(event)
            dump(folder / "tool_events.json", events)
            print(
                f"TOOL {len(events)} elapsed={time.monotonic() - start:.1f}s changes={event['tracked_changes'].strip()!r}",
                flush=True,
            )

    def shell_plain(command):
        return original({"command": command})["output"]

    env.execute = execute
    agent = AmbigAgent(
        model,
        env,
        reporter=None,
        system_template=(system_template or SYSTEM)
        + "\nNo further user replies are available.",
        instance_template="{{task}}",
        step_limit=100,
        cost_limit=0,
        wall_time_limit_seconds=7200,
        output_path=folder / "trajectory.json",
    )
    agent.instance_id = IID
    status, error = "NotStarted", None
    try:
        status = agent.run((folder / "task.txt").read_text()).get("exit_status")
    except Exception as exc:
        status, error = type(exc).__name__, str(exc)
    finally:
        duration = time.monotonic() - start
        env.execute = original
        patch = extract_patch(env, manifest["base_commit"])
        (folder / "model.patch").write_text(patch)
        post_import = python(
            env,
            "import django,pathlib,json; print(json.dumps({'django_import':str(pathlib.Path(django.__file__).resolve()),'cwd':str(pathlib.Path.cwd())}))",
        )
        dump(folder / "post_import.json", json.loads(post_import))
        agent.save(
            folder / "trajectory.json",
            {"info": {"exit_status": status, "error": error}},
        )
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        responses = []
        format_errors = 0
        for message in agent.messages:
            extra = message.get("extra", {})
            response = extra.get("response")
            format_errors += extra.get("interrupt_type") == "FormatError"
            if isinstance(response, dict):
                responses.append(response)
                for key in usage:
                    usage[key] += (response.get("usage") or {}).get(key, 0) or 0
        first = next((e for e in events if e["tracked_changes"].strip()), None)
        result = {
            "instance_id": IID,
            "exit_status": status,
            "error": error,
            "model_calls": agent.n_calls,
            "response_count": len(responses),
            "returned_models": sorted({r.get("model", "") for r in responses}),
            "tool_calls": len(events),
            "usage": usage,
            "format_errors": format_errors,
            "elapsed_seconds": duration,
            "first_tracked_change": {
                "tool_call": first["tool_call"],
                "elapsed_seconds": first["elapsed_start_seconds"]
                + first["duration_seconds"],
                "paths": first["tracked_changes"].splitlines(),
            }
            if first
            else None,
            "patch_bytes": len(patch.encode()),
            "finished_at": now(),
        }
        dump(folder / "result.json", result)
        dump(
            folder / "predictions.json",
            {
                "instance_id": IID,
                "model_name_or_path": "mini-qwen38-ideal-no-followup",
                "model_patch": patch,
            },
        )
        env.cleanup()
        if hasattr(model, "close"):
            model.close()
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "run"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.stage == "prepare" else run)(args.output.resolve())
