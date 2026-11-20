# Qwen native-tool protocol correction

This experiment changes the model/tool interface only. It uses upstream mini's
`LitellmModel` and Bash schema, requires one native Bash call per response, and
returns observations as `role=tool` with the original `tool_call_id`.
`NATIVE_SYSTEM` replaces only the text command-format paragraph; all task data
is byte-identical to the previous 10554 ideal/no-followup run.

`NativeBenchmarkModel` captures actual HTTP JSON requests and responses without
authentication headers. It validates commands before execution and stops on a
malformed native response. API retries remain disabled. The generic mini runner
still retains its existing text-mode entry point; this independent experiment
explicitly selects the native model class.

Run from the original project root using `.venv-mini/bin/python`:

```bash
python -m evaluation.analysis.mini_10554_native_20261119.probe --output NEW_PROBE_DIR
python -m evaluation.analysis.mini_10554_native_20261119.run \
  --stage prepare --probe-dir NEW_PROBE_DIR --output NEW_RUN_DIR
python -m evaluation.analysis.mini_10554_native_20261119.run \
  --stage run --output NEW_RUN_DIR
```

Prepare repeats the prior no-model-call preflight, including reference-history
cleanup and actual import alignment. It verifies the exact same task, dependencies,
base commit and image ID. The run pauses after 20 executed model tool calls for
a host-only review. A review is not a user followup and must not add model messages.
Do not create a continuation of a prior solver trajectory.

The real endpoint probes cover reading, editing and failure return-code handling.
The first probe audit overconstrained the failure command to include a redundant
`bash -c` wrapper. The model executed the equivalent command in the environment's
existing Bash interpreter, returned 7, and correctly read it from the next tool
message. That assertion was corrected using the existing wire records, without
repeating model requests or changing transcripts; both original and corrected
audit records are retained.

The previous text run remains classified as a protocol-fault interruption with
official unresolved status, not a completed ability comparison. The native run
is independent. Its raw patch must be graded once in a separate official grader
container; grader messages and hidden tests must never be fed back to the solver.
