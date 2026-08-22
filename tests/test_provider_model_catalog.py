from src.webui.services.generation import _extract_model_ids, _model_list_url


def test_model_list_url_handles_openai_and_anthropic_bases():
    assert _model_list_url("https://api.openai.com/v1", "openai") == "https://api.openai.com/v1/models"
    assert _model_list_url("https://api.example/v1/chat/completions", "openai") == "https://api.example/v1/models"
    assert _model_list_url("https://api.anthropic.com", "anthropic") == "https://api.anthropic.com/v1/models"


def test_extract_model_ids_accepts_common_response_shapes():
    assert _extract_model_ids({"data": [{"id": "z"}, {"id": "a"}, {"id": "z"}]}) == ["a", "z"]
    assert _extract_model_ids({"models": [{"name": "beta"}, {"model": "alpha"}]}) == ["alpha", "beta"]
