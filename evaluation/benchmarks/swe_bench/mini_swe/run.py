"""Run Ambig-SWE with mini-SWE-agent; never imports OpenHands."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib

PROJECT = Path(__file__).resolve().parents[4]


def load_instances(path: Path, ids=None, limit=None) -> list[dict]:
    import pandas as pd

    if path.suffix == ".xlsx":
        frame = pd.read_excel(path, dtype={"version": str})
    elif path.suffix == ".csv":
        frame = pd.read_csv(path, dtype={"version": str})
    else:
        frame = pd.read_json(path, lines=path.suffix == ".jsonl")
    rows = frame.fillna("").to_dict("records")
    if len({row["instance_id"] for row in rows}) != len(rows):
        raise ValueError("Dataset contains duplicate instance IDs")
    if ids:
        by_id = {row["instance_id"]: row for row in rows}
        if len(set(ids)) != len(ids) or set(ids) - by_id.keys():
            raise ValueError("Unknown or duplicate selected instance IDs")
        rows = [by_id[iid] for iid in ids]
    return rows[:limit] if limit else rows


def build_task(instance: dict, setting: str) -> str:
    if setting == "full":
        return (
            instance["original_issue"]
            + "\n\nLater discussion / hints:\n"
            + instance.get("hints_text", "")
        )
    return instance["problem_statement"]


def run_instance(instance, args, settings, proxy_settings, image):
    from evaluation.benchmarks.swe_bench.mini_swe.agent import (
        AmbigAgent,
        INTERACTION,
        SYSTEM,
    )
    from evaluation.benchmarks.swe_bench.mini_swe.model import make_model
    from evaluation.benchmarks.swe_bench.mini_swe.reporter import Reporter
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import (
        extract_patch,
        initialize,
        start_environment,
    )

    iid = instance["instance_id"]
    folder = args.output / iid
    folder.mkdir(parents=True, exist_ok=True)
    env = agent = reporter = None
    patch, error, status, startup = "", None, "NotStarted", ""
    task = build_task(instance, args.setting)
    try:
        system = SYSTEM
        if args.setting == "interaction":
            fixture = (
                json.loads(args.response_fixture.read_text())
                if args.response_fixture
                else None
            )
            reporter = Reporter(
                instance,
                proxy_settings,
                args.output / "interaction_logs" / f"{iid}.json",
                fixture,
                args.response_condition,
            )
            if reporter.initial:
                task += "\n\nRecorded initial clarification:\n" + json.dumps(
                    reporter.initial, ensure_ascii=False
                )
            system += (
                INTERACTION
                if not reporter.initial
                else INTERACTION.replace(
                    "As your first action, ask specific clarification questions.",
                    "Use the recorded initial answer. Ask followups only if needed.",
                )
            )
            if args.clarification_mode == "initial-only":
                system = (
                    SYSTEM
                    + "\nUse the recorded clarification; no further reporter replies are available."
                )
                reporter.controlled.clarification_closed = True
        env = start_environment(image, args.command_timeout, args.network)
        startup = initialize(env, instance)
        agent = AmbigAgent(
            make_model(settings),
            env,
            reporter=reporter,
            system_template=system,
            instance_template="{{task}}",
            step_limit=args.max_iterations,
            cost_limit=0,
            wall_time_limit_seconds=args.wall_time_limit,
            output_path=folder / f"{iid}.traj.json",
        )
        agent.instance_id = iid
        status = agent.run(task)["exit_status"]
    except Exception as exc:
        status, error = type(exc).__name__, str(exc)
    finally:
        if env is not None:
            try:
                patch = extract_patch(env, instance["base_commit"])
            except Exception as exc:
                error = (error or "") + f" Patch extraction failed: {exc}"
            try:
                env.cleanup()
            except Exception as exc:
                error = (error or "") + f" Container cleanup failed: {exc}"
    result = {
        "instance_id": iid,
        "instruction": task,
        "git_patch": patch,
        "test_result": {
            "git_patch": patch,
            "interactions": reporter.interactions if reporter else [],
        },
        "num_turns": len(reporter.interactions) if reporter else 0,
        "metrics": {
            "accumulated_cost": agent.cost if agent else 0,
            "api_calls": agent.n_calls if agent else 0,
        },
        "metadata": {
            "agent_class": "mini-SWE-agent",
            "setting": args.setting,
            "model": settings["model"],
            "image": image,
            "exit_status": status,
            "startup": startup,
        },
        "error": error,
    }
    if agent:
        agent.save(
            folder / f"{iid}.traj.json",
            {"info": {"exit_status": status, "error": error}},
        )
    (folder / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )
    print(
        f"{iid}: {status}, calls={result['metrics']['api_calls']}, patch={len(patch)} bytes",
        flush=True,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--setting", choices=("full", "hidden", "interaction"), default="interaction"
    )
    parser.add_argument("--llm-config", default="hidden_deepseek_flash")
    parser.add_argument("--proxy-llm-config", default="hidden_deepseek_flash")
    parser.add_argument("--config", type=Path, default=PROJECT / "config.toml")
    parser.add_argument(
        "--data",
        "--csv_file",
        type=Path,
        default=PROJECT
        / "evaluation/benchmarks/swe_bench/data/full_summaries_verified.xlsx",
    )
    parser.add_argument("--instance-ids", nargs="+")
    parser.add_argument("--eval-n-limit", type=int)
    parser.add_argument("--max-iterations", type=int, default=100)
    parser.add_argument("--workers", "--eval-num-workers", type=int, default=1)
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--wall-time-limit", type=int, default=7200)
    parser.add_argument(
        "--network",
        default="none",
        help="Docker network, defaults to offline execution",
    )
    parser.add_argument(
        "--image-template", help="Local image name containing {instance_id}"
    )
    parser.add_argument("--output", "--eval-output-dir", type=Path)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate data/config without Docker or API access",
    )
    parser.add_argument(
        "--check-images", action="store_true", help="Also verify local Docker images"
    )
    parser.add_argument("--response-fixture", type=Path)
    parser.add_argument("--response-condition", choices=("ideal", "partial"))
    parser.add_argument(
        "--clarification-mode", choices=("adaptive", "initial-only"), default="adaptive"
    )
    args = parser.parse_args()
    if (
        min(
            args.workers,
            args.max_iterations,
            args.command_timeout,
            args.wall_time_limit,
        )
        < 1
    ):
        parser.error("Workers and time/step limits must be positive")
    if args.eval_n_limit is not None and args.eval_n_limit < 1:
        parser.error("--eval-n-limit must be positive")
    if bool(args.response_fixture) != bool(args.response_condition):
        parser.error("Supply both --response-fixture and --response-condition")
    if args.response_fixture and args.setting != "interaction":
        parser.error("Fixtures require --setting interaction")
    if args.clarification_mode == "initial-only" and not args.response_fixture:
        parser.error("initial-only requires a fixed response fixture")
    config = tomllib.loads(args.config.read_text())
    settings = config["llm"][args.llm_config.removeprefix("llm.")]
    proxy_settings = (
        config["llm"][args.proxy_llm_config.removeprefix("llm.")]
        if args.setting == "interaction"
        else None
    )
    rows = load_instances(args.data, args.instance_ids, args.eval_n_limit)
    if not rows:
        parser.error("No selected instances")
    for row in rows:
        for key in ("repo", "base_commit", "original_issue", "problem_statement"):
            if not row.get(key):
                parser.error(f"{row['instance_id']} is missing {key}")
    if args.response_fixture:
        fixture = json.loads(args.response_fixture.read_text())
        if len(rows) != 1 or any(
            (
                fixture["instance_id"] != rows[0]["instance_id"],
                fixture["base_commit"] != rows[0]["base_commit"],
                fixture["public_task"]["summary"] != rows[0]["problem_statement"],
            )
        ):
            parser.error(
                "Fixture must match exactly one selected task, commit and summary"
            )
    print(
        json.dumps(
            {
                "setting": args.setting,
                "agent_model": settings["model"],
                "proxy_model": proxy_settings["model"] if proxy_settings else None,
                "instance_ids": [row["instance_id"] for row in rows],
                "step_limit": args.max_iterations,
                "action_protocol": "text-based-bash",
            },
            indent=2,
        )
    )
    images = {}
    if not args.check_only or args.check_images:
        from evaluation.benchmarks.swe_bench.mini_swe.runtime import find_image

        images = {
            row["instance_id"]: find_image(row["instance_id"], args.image_template)
            for row in rows
        }
        print(json.dumps(images, indent=2))
    if args.check_only:
        return 0
    args.output = args.output or PROJECT / "evaluation/evaluation_outputs/mini_swe" / (
        args.setting + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    args.output.mkdir(parents=True, exist_ok=True)
    # Refuse resuming with a different experiment instead of mixing predictions.
    manifest = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(args).items()
        if key not in ("check_only", "check_images", "workers")
    }
    manifest["data_sha256"] = hashlib.sha256(args.data.read_bytes()).hexdigest()
    manifest["fixture_sha256"] = (
        hashlib.sha256(args.response_fixture.read_bytes()).hexdigest()
        if args.response_fixture
        else None
    )
    manifest["model_settings"] = {
        key: value for key, value in settings.items() if "key" not in key
    }
    manifest["proxy_settings"] = {
        key: value for key, value in (proxy_settings or {}).items() if "key" not in key
    }
    manifest_path = args.output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        parser.error("Output directory belongs to a different experiment")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    results = {}
    for row in rows:
        path = args.output / row["instance_id"] / "result.json"
        if path.exists():
            results[row["instance_id"]] = json.loads(path.read_text())

    def save_outputs():
        ordered = [
            results[row["instance_id"]] for row in rows if row["instance_id"] in results
        ]
        for name, values in [
            ("output.jsonl", ordered),
            (
                "predictions.jsonl",
                [
                    {
                        "instance_id": r["instance_id"],
                        "model_name_or_path": settings["model"],
                        "model_patch": r["git_patch"],
                    }
                    for r in ordered
                ],
            ),
        ]:
            temporary = args.output / (name + ".tmp")
            temporary.write_text(
                "".join(
                    json.dumps(value, ensure_ascii=False) + "\n" for value in values
                )
            )
            temporary.replace(args.output / name)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                run_instance,
                row,
                args,
                settings,
                proxy_settings,
                images[row["instance_id"]],
            )
            for row in rows
            if row["instance_id"] not in results
        ]
        for future in as_completed(futures):
            result = future.result()
            results[result["instance_id"]] = result
            save_outputs()
    save_outputs()
    print(f"Results: {args.output}")
    return int(any(r["error"] for r in results.values()))


if __name__ == "__main__":
    os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
    os.environ.setdefault("MSWEA_GLOBAL_CONFIG_DIR", "/tmp/ambig-mini-config")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    sys.exit(main())
