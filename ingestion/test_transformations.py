import pytest

from models import TransformationType
from transformations import LLMParams, LLMScoreTransformOutput, validate_transform_config


def test_llmparams_typed_and_passthrough() -> None:
    params = LLMParams.model_validate({"temperature": 0.2, "min_p": 0.05})
    assert params.model_dump(exclude_none=True) == {"temperature": 0.2, "min_p": 0.05}


def test_score_output_parses_json() -> None:
    out = LLMScoreTransformOutput.model_validate_json('{"score": 7, "rationale": "ok"}')
    assert out.score == 7
    assert out.rationale == "ok"


def test_validate_score_config_ok() -> None:
    validate_transform_config("score", "openai/gpt-5-nano", "prompt?", None, name="x")


def test_validate_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unknown transform type"):
        validate_transform_config("nope", "m", "p", None, name="x")


def test_validate_score_requires_model() -> None:
    with pytest.raises(ValueError, match="requires a model"):
        validate_transform_config("score", None, "p", None, name="x")


def test_validate_score_requires_prompt() -> None:
    with pytest.raises(ValueError, match="requires a prompt"):
        validate_transform_config("score", "m", "", None, name="x")


def test_validate_knowledge_requires_entity_types_when_missing() -> None:
    with pytest.raises(ValueError, match="entity_types"):
        validate_transform_config(TransformationType.KNOWLEDGE.value, "m", "p", None, name="x")


def test_validate_knowledge_requires_entity_types_when_empty() -> None:
    with pytest.raises(ValueError, match="entity_types"):
        validate_transform_config(TransformationType.KNOWLEDGE.value, "m", "p", {"entity_types": []}, name="x")


def test_validate_knowledge_config_ok() -> None:
    validate_transform_config(TransformationType.KNOWLEDGE.value, "m", "p", {"entity_types": ["Person"]}, name="x")


def test_validate_requires_name() -> None:
    with pytest.raises(ValueError, match="name"):
        validate_transform_config("score", "m", "p", None, "")


def test_validate_rejects_bad_gate_op() -> None:
    with pytest.raises(ValueError, match="op"):
        validate_transform_config(
            "score", "m", "p", None, "sc", {"source": "x", "field": "f", "op": "bogus", "value": 1}
        )
