# Skill Intelligent Evolution Pipeline - 总结文档

## 概述

Skill Intelligent Evolution Pipeline 是一个基于多用户 session 数据的技能进化系统。通过聚合 N 个执行会话作为集体证据，利用 LLM 分析执行模式并自动生成技能改进建议，最终产出进化版 SKILL.md。

核心创新点：不同于 OpenSpace 逐个处理单次执行，本系统将多个 session 聚合为证据集进行集体分析，能发现跨会话的共性问题和改进方向。

## 架构

```
sessions.jsonl → [Extract] → [Filter] → [Split] → [ProtoExtract] → [EvidenceBuild] → [LLM Analyze] → [LLM Evolve]
   (索引入口)       ↓ 查找                              ↓                ↓
  session_path → JSONL 文件                         证据文本          进化建议
                                                  (~1.5KB)        (fix/derived)
                                                                        ↓
                                                              staging/{skill_name}/
```

## 七个阶段

### Stage 1: Extract（提取）

以 `sessions.jsonl` 为入口，逐条查找对应的 JSONL 文件并解析为 `CanonicalSession`。

- 输入：`sessions.jsonl`（索引，含 session_path）+ 对应的 JSONL 文件
- 输出：`list[CanonicalSession]`
- 流程：读取索引 → 去重 → 逐条查找文件 → 解析 → 注入元数据（quality_score 等）
- 容错：文件不存在时打印 WARN 并跳过，不阻塞运行
- 代码：`skill_evolution/pipeline/runner.py` + `skill_evolution/extraction/session_extractor.py`

### Stage 2: Filter（过滤）

质量过滤和分类，丢弃空会话、低相关性、无工具调用的 session。

- 输入：全量 sessions
- 输出：通过质量检查的 session 分组
- 代码：`skill_evolution/processing/quality_filter.py`

### Stage 3: Split（拆分）

按 70/30 比例拆分为进化集和测试集。

- 输入：过滤后的 session 分组
- 输出：evolution_set（用于进化）+ test_set（用于验证）
- 代码：`skill_evolution/processing/sampler.py`

### Stage 4: ProtoExtract（结构化提取）

将每个 session 压缩为 ~500B 的 `ProtoAnalysis` 结构化摘要（纯代码，无 LLM）。

- 输入：`list[CanonicalSession]`
- 输出：`list[ProtoAnalysis]`
- 关键字段：status, task_title, tool_sequence, failure_reason, correction, token_usage
- 代码：`skill_evolution/extraction/proto_extractor.py`

### Stage 5: EvidenceBuild（证据构建）

将 N 个 ProtoAnalysis 格式化为一段证据文本（纯代码，无 LLM）。

- 输入：`list[ProtoAnalysis]`
- 输出：证据文本字符串（~500B/session）
- 包含：状态摘要、各 session 详情、聚合统计
- 代码：`skill_evolution/llm/evidence_builder.py`

### Stage 6: LLM Analyze（LLM 分析）

单次 LLM 调用，分析证据集并输出 `ExecutionAnalysis`。

- 输入：证据文本 + skill 名称 + 提示词模板（从 `prompts/*.txt` 加载）
- 输出：`ExecutionAnalysis`（含 `evolution_suggestions[]`）
- 建议类型：fix（原地修复）、derived（增强版）
- 代码：`skill_evolution/llm/evidence_analyzer.py`

### Stage 7: LLM Evolve（LLM 进化）

串行处理每个 `evolution_suggestion`，每次一个 LLM 调用。

- 输入：`ExecutionAnalysis` + 原始 SKILL.md 内容 + 提示词模板
- 输出：进化后的 SKILL.md 文件
- FIX：在原文件上应用 patch
- DERIVED：创建新的增强版 skill 目录（写入 staging）
- 代码：`skill_evolution/llm/skill_evolver.py` + `skill_evolution/evolution/patch.py`

## 目录结构

```
Self-Evolution-Pipeline/                    # 项目根目录
├── agent-*.jsonl                           # 原始 session 数据
├── 协议分析-agent/                          # skill 文件夹
│   ├── sessions.jsonl                      # session 索引（533 条）
│   └── .claude/skills/协议分析-agent/
│       └── SKILL.md                        # 技能定义文件
└── skill-evolution/                        # 流水线（代码 + 输出）
    ├── pyproject.toml
    ├── .env                                # API 密钥
    ├── configs/
    │   └── default.yaml                    # 全量配置（含 paths 段）
    ├── prompts/                            # 外置提示词模板
    │   ├── evidence_analysis_system.txt
    │   ├── evidence_analysis_user.txt
    │   ├── evolution_fix.txt
    │   └── evolution_derived.txt
    ├── skill_evolution/                    # Python 包
    │   ├── config/
    │   │   ├── settings.py                 # 配置定义（PathConfig 生效）
    │   │   └── prompts.py                  # PromptLoader
    │   ├── models/
    │   │   ├── session.py                  # CanonicalSession
    │   │   ├── skill.py                    # SkillVersion, EvaluationReport
    │   │   ├── evolution.py                # EvolutionType, EvolutionSuggestion
    │   │   └── proto_analysis.py           # ProtoAnalysis
    │   ├── extraction/
    │   │   ├── session_extractor.py        # JSONL → CanonicalSession
    │   │   ├── feedback_extractor.py       # sessions.jsonl 读取
    │   │   └── proto_extractor.py          # Session → ProtoAnalysis
    │   ├── processing/
    │   │   ├── quality_filter.py           # 质量过滤
    │   │   └── sampler.py                  # 数据集拆分
    │   ├── llm/
    │   │   ├── evidence_builder.py         # 证据文本构建
    │   │   ├── evidence_analyzer.py        # LLM 分析
    │   │   ├── skill_evolver.py            # LLM 进化
    │   │   └── prompts.py                  # 常量 + schema
    │   ├── evolution/
    │   │   └── patch.py                    # patch 引擎 + 模糊匹配
    │   └── pipeline/
    │       ├── cli.py                      # CLI 解析 + .env 加载
    │       └── runner.py                   # 流水线 7 阶段编排
    └── output/
        ├── runs/run_YYYYMMDD_HHMMSS/       # 每次运行的中间产物
        │   ├── run_meta.json
        │   ├── evolution_evidence.json
        │   ├── test_set.json
        │   ├── evidence_text.json
        │   ├── execution_analysis.json
        │   └── evolution_results.json
        └── staging/{skill_name}/           # 进化产物暂存区（待审核）
            └── {skill}-enhanced/SKILL.md
```

## 配置

所有路径通过 `configs/default.yaml` 的 `paths` 段配置，部署时只需编辑此文件：

```yaml
paths:
  project_root: ""                          # 空=自动检测，或填绝对路径
  session_glob: "agent-*.jsonl"             # session 文件匹配模式
  session_index: "{folder_name}/sessions.jsonl"  # 索引路径
  skill_search_paths:                       # SKILL.md 搜索路径
    - "{folder_name}/SKILL.md"
    - "{folder_name}/.claude/skills/{folder_name}/SKILL.md"
  skill_folder_map:                         # skill_name → 文件夹名映射
    protocol-agent: "协议分析-agent"
  output_dir: "output/runs"                 # 运行输出（相对于 skill-evolution/）
  staging_dir: "output/staging"             # 进化产物暂存（相对于 skill-evolution/）
  prompts_dir: "prompts"                    # 提示词模板（相对于 skill-evolution/）
```

## 运行方式

```bash
cd Self-Evolution-Pipeline/skill-evolution

# 运行完整流水线
python -m skill_evolution.pipeline.runner

# 指定 skill
python -m skill_evolution.pipeline.runner --skill protocol-agent

# 指定项目根目录
python -m skill_evolution.pipeline.runner --project-root /path/to/project

# 只运行提取阶段
python -m skill_evolution.pipeline.runner --stage extract

# 只运行分析+进化阶段
python -m skill_evolution.pipeline.runner --stage analyze

# 覆盖 staging 目录
python -m skill_evolution.pipeline.runner --staging-dir /path/to/staging
```

## Staging 工作流

```
流水线运行 → 写入 output/staging/{skill_name}/
                ├── {skill}-enhanced/SKILL.md   # DERIVED 产物
                └── (FIX 产物直接修改原文件)

人工审核 staging/ → 批准/拒绝/修改

未来：skill-evolve merge 命令将批准的产物合入正式 skill 目录
```

## 已知问题

1. **FIX 原地修改**：FIX 进化会直接修改原始 SKILL.md，建议运行前备份或使用 git 管理
2. **Windows 终端编码**：中文字符在部分终端显示为乱码（不影响功能）
3. **远程 session 跳过**：sessions.jsonl 中的远程路径在本地不可用时会打印大量 SKIP 日志

## 依赖

- Python 3.10+
- anthropic（LLM 调用）
- PyYAML（配置解析）
- 无其他外部依赖
