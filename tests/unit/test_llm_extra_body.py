from openhands.core.config import AppConfig, LLMConfig
from openhands.core.config.utils import load_from_toml
from openhands.llm.llm import LLM


def test_provider_body_survives_config_loading_and_reaches_completion(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('''[core]
workspace_base = "."
[llm]
model = "gpt-4o"
[llm.extra_body.thinking]
type = "enabled"
[llm.flash]
model = "gpt-4o"
[llm.flash.extra_body.thinking]
type = "disabled"
''')
    app = AppConfig()
    load_from_toml(app, str(path))
    assert app.get_llm_config('llm').extra_body == {'thinking': {'type': 'enabled'}}
    config = app.get_llm_config('flash')
    assert config.extra_body == {'thinking': {'type': 'disabled'}}
    assert LLM(config)._completion_unwrapped.keywords['extra_body'] == config.extra_body


def test_default_requests_do_not_add_provider_body():
    assert 'extra_body' not in LLM(LLMConfig(model='gpt-4o'))._completion_unwrapped.keywords
