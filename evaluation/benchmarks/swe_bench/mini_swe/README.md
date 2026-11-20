# Ambig-SWE with mini-SWE-agent

This adapter uses upstream mini-SWE-agent 2.4.6's `DefaultAgent`, text action
parser, model interface and Docker environment. It never imports OpenHands.
The checkout in `/home/rwh/mini-SWE-agent` is pinned initially to
`04d809ceab9df28f9adaed044884180159172930`.

## Installed environment

The separate Python environment is `/home/rwh/Ambig-SWE-Origin/.venv-mini`.
It inherits existing packages from `ambig-swe`, with mini's newer dependencies
installed only in this environment. The original OpenHands environment is unchanged.
To install elsewhere, create a Python 3.11+ environment and install
`requirements.txt`. Set `MINISWE_PYTHON` to its Python executable.

From the original project root:

```bash
bash evaluation/benchmarks/swe_bench/scripts/run_mini_swe.sh \
  --setting hidden --llm-config hidden_deepseek_flash \
  --instance-ids astropy__astropy-12907 --max-iterations 100
```

Or use `/home/rwh/mini-SWE-agent/run_ambigswe.sh` with the same arguments.
`--llm-config` reads existing groups from the original `config.toml`, including
base URL, sampling, token limit, and `extra_body`. API keys are excluded from
saved model configurations. No credentials are copied into containers.
For models without registered pricing, the reported cost can be zero (unknown),
not free usage. Raw API usage remains in trajectories; the runner enforces step
and wall-time limits rather than an estimated dollar budget.

## Benchmark settings

* `--setting full`: original issue plus hints, matching the original base setting.
* `--setting hidden`: only `problem_statement`, matching hidden inference.
* `--setting interaction`: summarized issue and up to three reporter answers.
  The first action asks a question; later questions are optional. Reporter inputs
  are original issue, hints, and the dataset's files field, as in the old runner.
  Use `--proxy-llm-config hidden_deepseek_flash` to use DeepSeek for the reporter too.

Repair actions use exactly one `mswea_bash_command` block. The sole extension is
`ask_user 'question'`, intercepted on the host and counted against the same
three-answer cap. Each repair model call, including questions and format errors,
counts toward `--max-iterations`. Three consecutive malformed responses end the run.
The proxy's API calls do not consume repair steps; its usage is logged separately.
This is a framework comparison with adapted prompts, not a claim of byte-identical
OpenHands prompting. The old answer checker and diagnosis-specific repair prompts
are not enabled. Fixed initial Q/A can be reused with:

```bash
bash evaluation/benchmarks/swe_bench/scripts/run_mini_swe.sh \
  --setting interaction --llm-config hidden_deepseek_flash \
  --proxy-llm-config hidden_deepseek_flash \
  --instance-ids django__django-10554 \
  --response-fixture /absolute/path/to/fixture.json \
  --response-condition partial --clarification-mode initial-only
```

The fixture must match the selected task, base commit, and public summary.
`adaptive` permits followups through the original `ControlledProxy` implementation.

## Data and execution

The default is the original 500-row `full_summaries_verified.xlsx`.
`--data` accepts XLSX, CSV, JSON, or JSONL. Explicit `--instance-ids` preserve order;
`--eval-n-limit` applies afterward. With neither option, all rows are selected.
Use `--workers` for independent instance containers.

```bash
# No containers, API calls, image builds, or downloads:
bash evaluation/benchmarks/swe_bench/scripts/run_mini_swe.sh \
  --llm-config hidden_deepseek_flash --check-only --instance-ids django__django-10554

# Also verify locally cached images (requires Docker access):
bash evaluation/benchmarks/swe_bench/scripts/run_mini_swe.sh \
  --llm-config hidden_deepseek_flash --check-only --check-images \
  --instance-ids django__django-10554
```

Image selection prefers existing `sweb.eval.x86_64.<instance_id>:latest`, then
the project's Xingyao image, then the official SWE-bench registry name.
Images are never automatically downloaded. `--image-template` overrides selection.
Commands run directly in `/testbed`, with `conda activate testbed` on every action.
The container resets to `base_commit`, removes remotes and reference Git history,
and does not receive gold patches or held-out tests. The default network is `none`.
Commands have an in-container timeout that kills the process group; a failed Docker
transport stops the run. Cleanup removes only the container owned by that instance.

## Results and evaluation

Use `--output /absolute/path` for a stable destination. Reusing it resumes completed
results; incompatible experiment settings are rejected. Failed results are retained
for diagnosis; use a new directory to rerun them.

* `output.jsonl`: original-style `git_patch`, `test_result.git_patch`, metrics,
  interactions, status, and errors.
* `predictions.jsonl`: official SWE-bench `instance_id`, `model_name_or_path`, `model_patch`.
* `<instance_id>/*.traj.json`: upstream trajectory saved after every step.
* `interaction_logs/`: reporter exchanges, kept on the host.
* `manifest.json`: experiment settings without credentials.

Patch extraction reads the actual workspace against `base_commit`, with a separate
Git index. It works on submission, step exhaustion, or model failure, includes
untracked files, and does not rely on the model printing a patch. Temporary scripts
left in the repo are also included, as in the original extraction; agents should
delete them before submission. A nonempty patch alone does not imply a solved task.

The existing grader accepts the predictions directly:

```bash
bash evaluation/benchmarks/swe_bench/scripts/eval_infer.sh \
  /absolute/path/to/predictions.jsonl astropy__astropy-12907 \
  princeton-nlp/SWE-bench_Verified test
```

Run that command in the original project's Poetry environment. Alternatively use
`python -m swebench.harness.run_evaluation --predictions_path ...` in a SWE-bench
grader environment. Grading runs separately and does not expose held-out tests
to the repair model.

## Adapter checks

```bash
MSWEA_SILENT_STARTUP=1 /home/rwh/Ambig-SWE-Origin/.venv-mini/bin/python \
  -m pytest tests/unit/test_mini_swe.py -q
```

Checks exercise the upstream loop's budget stop after a real local source edit,
independent patch extraction including untracked files, reporter privacy and caps,
submission, text parsing, dataset selection, and credential redaction.
