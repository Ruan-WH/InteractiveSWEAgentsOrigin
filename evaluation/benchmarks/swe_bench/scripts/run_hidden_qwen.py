"""Run the original hidden workflow with Qwen3-Coder-Flash native tool calling.

From the project root:
    PYTHONPATH=. /home/rwh/miniconda3/envs/ambig-swe/bin/python \
        evaluation/benchmarks/swe_bench/scripts/run_hidden_qwen.py

Uses llm.hidden_qwen_coder and hidden_experiment.selected_ids in config.toml.
Extra evaluator arguments override the defaults, e.g. --eval-n-limit 1.
No inference request is made until the original evaluator starts its agent.
"""

from run_hidden_flash import main


MODEL_METADATA = {
    'litellm_provider': 'openai',
    'mode': 'chat',
    'supports_function_calling': True,
    'supports_vision': False,
    'max_input_tokens': 997952,
    'max_output_tokens': 65536,
    'max_tokens': 1000000,
}


if __name__ == '__main__':
    main(
        llm_config_name='hidden_qwen_coder',
        eval_note='hidden-qwen3-coder-flash-native-validation-v2',
        model_metadata=MODEL_METADATA,
    )
