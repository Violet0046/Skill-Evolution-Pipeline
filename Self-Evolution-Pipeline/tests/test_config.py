"""Tests for configuration management — Pydantic v2 models and loader."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from skill_evolution.config.settings import (
    PipelineConfig,
    LLMConfig,
    ExtractionConfig,
    SamplingConfig,
    EvaluationConfig,
    PathConfig,
    load_config,
    get_config,
    reset_config,
    _deep_merge_dict,
)


class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.provider == "anthropic"
        assert cfg.max_retries == 3
        assert cfg.temperature == 0.0

    def test_invalid_provider(self):
        with pytest.raises(ValueError, match="Provider must be one of"):
            LLMConfig(provider="invalid")

    def test_invalid_temperature(self):
        with pytest.raises(ValueError):
            LLMConfig(temperature=3.0)


class TestSamplingConfig:
    def test_defaults(self):
        cfg = SamplingConfig()
        assert cfg.evolution_ratio == 0.70
        assert cfg.test_ratio == 0.30

    def test_invalid_ratio_sum(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            SamplingConfig(evolution_ratio=0.80, test_ratio=0.30)


class TestEvaluationConfig:
    def test_default_dimensions(self):
        cfg = EvaluationConfig()
        assert abs(sum(cfg.dimensions.values()) - 1.0) < 0.01

    def test_invalid_dimensions(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            EvaluationConfig(dimensions={"a": 0.5, "b": 0.3})


class TestPipelineConfig:
    def test_from_yaml(self, tmp_path: Path):
        config_data = {
            "skill_name": "my-skill",
            "llm": {"model": "gpt-4", "max_tokens": 2048},
            "sampling": {"min_relevance_score": 5},
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        cfg = PipelineConfig.from_yaml(str(config_file))
        assert cfg.skill_name == "my-skill"
        assert cfg.llm.model == "gpt-4"
        assert cfg.llm.max_tokens == 2048
        assert cfg.sampling.min_relevance_score == 5

    def test_model_dump(self):
        cfg = PipelineConfig(skill_name="test")
        data = cfg.model_dump()
        assert "skill_name" in data
        assert "llm" in data
        assert "sampling" in data

    def test_to_yaml(self, tmp_path: Path):
        cfg = PipelineConfig(skill_name="test")
        out_path = tmp_path / "out.yaml"
        cfg.to_yaml(str(out_path))
        assert out_path.exists()
        loaded = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert loaded["skill_name"] == "test"


class TestPathConfig:
    def test_get_project_root_raises_if_empty(self):
        cfg = PathConfig()
        with pytest.raises(ValueError, match="project_root is not set"):
            cfg.get_project_root()

    def test_get_folder_name(self):
        cfg = PathConfig()
        assert cfg.get_folder_name("protocol-agent") == "协议分析-agent"
        assert cfg.get_folder_name("unknown-skill") == "unknown-skill"


class TestDeepMerge:
    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        update = {"b": 3, "c": 4}
        result = _deep_merge_dict(base, update)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}}
        update = {"a": {"y": 3, "z": 4}}
        result = _deep_merge_dict(base, update)
        assert result == {"a": {"x": 1, "y": 3, "z": 4}}


class TestConfigLoader:
    def test_load_config_default(self, tmp_path: Path, monkeypatch):
        """Test loading config from a YAML file."""
        config_data = {"skill_name": "loaded-skill"}
        config_file = tmp_path / "default.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        reset_config()
        cfg = load_config(str(config_file))
        assert cfg.skill_name == "loaded-skill"

    def test_reset_config(self):
        """Test that reset_config clears the singleton."""
        load_config()
        reset_config()
        # After reset, get_config should reload
        cfg = get_config()
        assert cfg is not None
