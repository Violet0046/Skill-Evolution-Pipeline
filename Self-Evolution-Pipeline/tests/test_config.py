"""配置系统测试 — 验证 YAML 加载、环境变量覆盖、验证规则。

=== 配置层级 ===

  PipelineConfig (顶层)
    ├── skill_name: str
    ├── llm: LLMConfig
    │     ├── provider: "openai" | "anthropic"  ← 由 LLM_PROVIDER 环境变量决定
    │     ├── model: str                         ← 由 OPENAI_MODEL / ANTHROPIC_MODEL 决定
    │     ├── api_key: str                       ← 由 OPENAI_API_KEY / ANTHROPIC_API_KEY 决定
    │     └── api_base: str                      ← 由 OPENAI_API_BASE / ANTHROPIC_API_BASE 决定
    ├── extraction: ExtractionConfig
    ├── sampling: SamplingConfig
    ├── evaluation: EvaluationConfig
    └── paths: PathConfig

=== 环境变量优先级 ===

  .env 文件 → os.getenv() → LLMConfig.__init__() 覆盖 YAML 值
  测试时需要隔离环境变量，使用 monkeypatch.delenv / monkeypatch.setenv
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from skill_evolution.config.settings import (
    PipelineConfig,
    LLMConfig,
    SamplingConfig,
    EvaluationConfig,
    PathConfig,
    load_config,
    reset_config,
    _deep_merge_dict,
)


# ═══════════════════════════════════════════════════════════════════════════════
# LLMConfig
# ═══════════════════════════════════════════════════════════════════════════════

class TestLLMConfig:
    """验证 LLM 配置的初始化和验证规则。"""

    def test_defaults_with_env(self, monkeypatch):
        """默认情况下应该从环境变量读取 provider 和 model。

        注意: 测试时需要设置环境变量，因为 LLMConfig.__init__ 会读取它们。
        """
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_MODEL", "test-model")
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)

        cfg = LLMConfig()
        assert cfg.provider == "openai"
        assert cfg.model == "test-model"
        assert cfg.api_key == "test-key"

    def test_anthropic_provider(self, monkeypatch):
        """provider=anthropic 时应该读取 ANTHROPIC_* 环境变量。"""
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
        monkeypatch.delenv("ANTHROPIC_API_BASE", raising=False)

        cfg = LLMConfig(provider="anthropic")
        assert cfg.provider == "anthropic"
        assert cfg.api_key == "sk-test"
        assert cfg.model == "claude-test"

    def test_invalid_provider(self):
        """无效的 provider 应该抛出 ValueError。"""
        with pytest.raises(ValueError, match="Provider must be one of"):
            LLMConfig(provider="invalid")

    def test_invalid_temperature(self):
        """温度超出范围应该抛出 ValueError。"""
        with pytest.raises(ValueError):
            LLMConfig(temperature=3.0)


# ═══════════════════════════════════════════════════════════════════════════════
# SamplingConfig
# ═══════════════════════════════════════════════════════════════════════════════

class TestSamplingConfig:
    """验证采样配置的验证规则。"""

    def test_defaults(self):
        """默认值: evolution_ratio=0.7, test_ratio=0.3, min_relevance_score=4。"""
        cfg = SamplingConfig()
        assert cfg.evolution_ratio == 0.7
        assert cfg.test_ratio == 0.3

    def test_invalid_ratio_sum(self):
        """evolution_ratio + test_ratio 必须等于 1.0。"""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            SamplingConfig(evolution_ratio=0.6, test_ratio=0.2)


# ═══════════════════════════════════════════════════════════════════════════════
# EvaluationConfig
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvaluationConfig:
    """验证评估配置。"""

    def test_default_dimensions(self):
        """默认维度权重应该有 4 项，总和为 1.0。"""
        cfg = EvaluationConfig()
        assert len(cfg.dimensions) == 4
        assert abs(sum(cfg.dimensions.values()) - 1.0) < 0.01

    def test_invalid_dimensions(self):
        """维度权重之和不等于 1.0 应该抛出 ValueError。"""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            EvaluationConfig(dimensions={"a": 0.5, "b": 0.3})


# ═══════════════════════════════════════════════════════════════════════════════
# PathConfig
# ═══════════════════════════════════════════════════════════════════════════════

class TestPathConfig:
    """验证路径配置。"""

    def test_get_project_root_raises_if_empty(self):
        """project_root 为空时应该抛出 ValueError。"""
        cfg = PathConfig(project_root="")
        with pytest.raises(ValueError, match="project_root is not set"):
            cfg.get_project_root()

    def test_get_folder_name(self):
        """get_folder_name() 应该使用 skill_folder_map 映射。"""
        cfg = PathConfig()
        # 默认映射: protocol-agent → 协议分析-agent
        assert cfg.get_folder_name("protocol-agent") == "协议分析-agent"
        # 未映射的 skill_name 应该原样返回
        assert cfg.get_folder_name("unknown-skill") == "unknown-skill"


# ═══════════════════════════════════════════════════════════════════════════════
# PipelineConfig
# ═══════════════════════════════════════════════════════════════════════════════

class TestPipelineConfig:
    """验证顶层配置。"""

    def test_from_yaml(self, tmp_path, monkeypatch):
        """from_yaml() 应该从 YAML 文件加载配置。

        测试时需要隔离环境变量，避免 .env 文件影响测试结果。
        """
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-4")
        monkeypatch.delenv("OPENAI_API_BASE", raising=False)

        config_data = {
            "skill_name": "test-skill",
            "llm": {
                "provider": "openai",
                "model": "gpt-4",
                "max_tokens": 2048,
            },
        }
        config_file = tmp_path / "test.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        cfg = PipelineConfig.from_yaml(str(config_file))
        assert cfg.skill_name == "test-skill"
        assert cfg.llm.model == "gpt-4"
        assert cfg.llm.max_tokens == 2048

    def test_model_dump(self):
        """model_dump() 应该返回可序列化的字典。"""
        cfg = PipelineConfig(skill_name="test")
        d = cfg.model_dump()
        assert "skill_name" in d
        assert "llm" in d
        assert "sampling" in d

    def test_get_skill_names(self):
        """get_skill_names() 优先返回 skill_names 列表，否则返回 [skill_name]。"""
        cfg = PipelineConfig(skill_name="single")
        assert cfg.get_skill_names() == ["single"]

        cfg = PipelineConfig(skill_name="single", skill_names=["a", "b"])
        assert cfg.get_skill_names() == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════════════════
# 深度合并
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeepMerge:
    """验证配置深度合并逻辑。"""

    def test_simple_merge(self):
        """简单键值应该被覆盖。"""
        base = {"a": 1, "b": 2}
        update = {"b": 3, "c": 4}
        result = _deep_merge_dict(base, update)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        """嵌套字典应该递归合并，而不是整体覆盖。"""
        base = {"llm": {"model": "old", "max_tokens": 1024}}
        update = {"llm": {"model": "new"}}
        result = _deep_merge_dict(base, update)
        assert result["llm"]["model"] == "new"
        assert result["llm"]["max_tokens"] == 1024  # 保留
