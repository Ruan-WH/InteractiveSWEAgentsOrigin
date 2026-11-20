"""Consolidation must use the declared prefix and preserve only public events."""
import json

from evaluation.analysis.model_swap_10554_diagnosis_20261119.consolidate_own_trace import capture, render


def test_trace_render_preserves_public_evidence_without_private_thoughts():
    messages = [
        {'role':'system','content':'scaffold boilerplate'},
        {'role':'user','content':[{'type':'text','text':'literal issue and Q/A'}]},
        {'role':'assistant','content':'public conclusion','reasoning_content':'private internal reasoning',
         'tool_calls':[{'function':{'name':'execute_bash','arguments':'{"command":"python repro.py"}'}}]},
        {'role':'tool','content':[{'type':'text','text':'observed failure\nexact newline'}]},
    ]
    text = render(messages)
    for value in ('literal issue and Q/A','public conclusion','python repro.py','observed failure\nexact newline'):
        assert value in text
    assert 'private internal reasoning' not in text
    assert 'scaffold boilerplate' not in text


def test_checkpoint_does_not_select_later_favorable_observations(tmp_path):
    folder = tmp_path / 'partial_followup/inference/llm_completions/task'
    folder.mkdir(parents=True)
    for count in range(1, 4):
        data = {'messages':[{'role':'assistant','tool_calls':[{}]*count},
                            {'role':'tool','content':f'observation {count}'}]}
        (folder / f'{count}.json').write_text(json.dumps(data))
    selected = capture(tmp_path, min_tools=2)
    assert selected[0].name == '2.json'
    assert selected[2] == 2
    assert 'observation 3' not in json.dumps(selected[1])
