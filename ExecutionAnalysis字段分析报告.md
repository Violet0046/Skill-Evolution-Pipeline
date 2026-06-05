# ExecutionAnalysis 字段分析报告

## 原始数据

- **来源**: `EvidenceAnalyzer.analyze()` (Stage 6: Analyze)
- **输入**: `evidence_text.md` (198个 ProtoAnalysis 的格式化文本)
- **输出**: `ExecutionAnalysis_output.json` (8KB)
- **调用 LLM**: MiniMax-M2.7

---

## 一、各字段作用详解

### 1. 汇总统计字段

| 字段 | 当前值 | 作用 | 对分析的价值 | 建议 |
|-----|-------|------|------------|------|
| `skill_name` | `查询需求信息` | 标识分析的技能名称 | 中 - 上下文信息 | 必须保留 |
| `total_sessions` | `198` | 总会话数量 | 高 - 统计基数 | 必须保留 |
| `success_count` | `191` | 成功执行的会话数 | 高 - 核心指标 | 必须保留 |
| `retry_success_count` | `2` | 重试后成功的会话数 | 中 - 反映自愈能力 | 建议保留 |
| `failed_count` | `5` | 失败的会话数 | 高 - 诊断重点 | 必须保留 |
| `success_rate` | `0.965` | 成功率 (success + retry_success) / total | 高 - 整体健康度 | 必须保留 |

**计算逻辑**:
```python
success_rate = (success_count + retry_success_count) / total_sessions
# = (191 + 2) / 198 = 0.965
```

### 2. dominant_patterns (主导模式)

**作用**: 从 198 个会话中提炼出的高频行为模式

| 字段 | 说明 |
|-----|------|
| `pattern` | 模式描述文本 |
| `frequency` | 出现次数 |
| `impact` | 影响程度: high/medium/low |
| `evidence` | 支撑此模式的 session_id 列表 (最多5个) |

**当前数据** (5个模式):

| 模式 | 频次 | 影响 | 说明 |
|-----|------|------|------|
| Skill 作为首技能调用 | 198 | high | 100%出现，最核心的执行路径 |
| Bash调用Python脚本读认证 | 198 | high | 100%出现，认证机制 |
| 查询成功输出需求信息 | 191 | high | 97%成功率 |
| Token无效时脚本失败 | 5 | medium | 失败模式 |
| Python脚本路径截断 | 5 | medium | 失败模式 |

**对分析的价值**: ⭐⭐⭐⭐⭐
- 帮助 LLM 快速理解技能的核心执行路径
- 区分正常模式 vs 异常模式

### 3. failure_analysis (失败分析)

**作用**: 深入分析失败会话，找出根本原因

```json
{
  "root_causes": [
    {
      "cause": "Token验证失败：.env文件中的认证信息无效或过期",
      "frequency": 3,
      "affected_sessions": ["08767333-864", "c03d2996-4c7", "f4e2f76c-72e"],
      "category": "tool_misuse"  // tool_misuse / rule_violation / logic_error / missing_step
    }
  ],
  "common_errors": [
    "Token验证失败",
    "Python脚本路径截断导致文件未找到",
    "pip install requests 依赖安装后仍无法解决认证问题"
  ]
}
```

| 子字段 | 作用 | 对进化的价值 |
|-------|------|------------|
| `root_causes` | 根本原因列表 | ⭐⭐⭐⭐⭐ 核心诊断信息 |
| `cause` | 原因描述 | 告诉开发者"什么坏了" |
| `frequency` | 出现次数 | 帮助判断优先级 |
| `affected_sessions` | 受影响的 session_id | 可追溯、可验证 |
| `category` | 原因分类 | 帮助确定修复策略 |
| `common_errors` | 高频错误列表 | 快速了解典型问题 |

**category 可选值**:
- `tool_misuse` - 工具使用错误 (如参数错误、路径错误)
- `rule_violation` - 违反规则
- `logic_error` - 逻辑错误
- `missing_step` - 缺少必要步骤

### 4. skill_gaps (技能缺口)

**作用**: 识别技能在规范/流程上的缺失或不足

```json
{
  "gap": "缺少对.env文件存在性的前置检查和友好的错误提示",
  "evidence": "多个会话显示当.env不存在时，脚本直接失败而非给出清晰的配置指引",
  "priority": "high"  // high / medium / low
}
```

| 字段 | 作用 | 对进化的价值 |
|-----|------|------------|
| `gap` | 缺失描述 | 告诉开发者"缺少什么" |
| `evidence` | 支撑证据 | 说明为什么需要这个功能 |
| `priority` | 优先级 | 帮助决定实现顺序 |

**当前数据** (4个缺口):

| 缺口 | 优先级 | 说明 |
|-----|--------|------|
| 缺少 .env 文件存在性检查 | high | 用户体验问题 |
| 缺少 Token 有效性自动校验 | high | 可靠性问题 |
| Python脚本路径依赖硬编码 | medium | 可移植性问题 |
| 缺少需求 ID 格式校验 | low | 输入验证问题 |

### 5. evolution_suggestions (进化建议)

**作用**: 基于证据集生成的具体修改建议

```json
{
  "type": "fix",           // fix / derived
  "direction": "在查询脚本开头增加.env文件存在性检查...",
  "target_skills": ["查询需求信息"],
  "category": "workflow",  // tool_guide / workflow / reference
  "evidence_sessions": ["08767333-864", "c03d2996-4c7"],
  "evidence_session_paths": ["/path/to/session1.jsonl", ...]
}
```

| 字段 | 作用 | 必须性 |
|-----|------|--------|
| `type` | 建议类型 | 必须 |
| `direction` | 具体修改方向 | **核心字段** |
| `target_skills` | 目标技能列表 | 必须 |
| `category` | 类别 | 可选 |
| `evidence_sessions` | 支撑证据 session_id | **关键** |
| `evidence_session_paths` | 完整路径，可供 LLM 工具读取 | **关键** |

**type 区别**:

| type | 含义 | 示例 |
|-----|------|------|
| `fix` | 修复现有问题 | "修复路径截断问题" |
| `derived` | 增强/派生新功能 | "增加 Token 自动校验" |

**当前数据** (4个建议):

| type | 方向 | category |
|-----|------|----------|
| fix | 增加 .env 存在性检查 | workflow |
| fix | 修复脚本路径拼接逻辑 | tool_guide |
| derived | 增加 Token 有效性自动校验 | workflow |
| derived | 增加需求 ID 格式校验 | tool_guide |

### 6. execution_efficiency (执行效率)

**作用**: 提供性能指标，用于优化参考

```json
{
  "avg_tokens": 2753352,
  "avg_duration_seconds": 9908.0,
  "bottleneck_tools": ["Bash", "Agent", "Task"]
}
```

| 字段 | 当前值 | 作用 | 建议 |
|-----|-------|------|------|
| `avg_tokens` | 2,753,352 | 平均每个会话的 token 消耗 | 可选保留 |
| `avg_duration_seconds` | 9,908秒 (~2.75小时) | 平均执行时长 | 可选保留 |
| `bottleneck_tools` | ["Bash", "Agent", "Task"] | 耗时最多的工具类型 | 可选保留 |

**问题**: 平均 token 275万非常高，可能是统计计算有误（198个会话的 token 应该求平均，不是直接加起来）

---

## 二、对后续 Stage 7 (Evolution) 的支撑

```
┌─────────────────────────────────────────────────────────────┐
│  Stage 6: ExecutionAnalysis                                  │
│                                                              │
│  evolution_suggestions[0] → Stage 7: run_evolution()         │
│  ├─ type: "fix"                                              │
│  ├─ direction: "增加 .env 存在性检查..."                      │
│  ├─ evidence_session_paths: [session1.jsonl, ...]            │
│  └─ category: "workflow"                                     │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Stage 7: SkillEvolver                                       │
│                                                              │
│  1. 读取 evidence_session_paths 中的完整会话数据             │
│  2. 理解当前错误和期望行为                                    │
│  3. 生成 skill 修改建议 (search/replace 补丁)                 │
│  4. 输出到 staging/{skill_name}/changes/{run_id}/            │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、字段完整性评估

### ✅ 完整的字段 (足够支撑进化)

| 字段 | 评估 |
|-----|------|
| `failure_analysis.root_causes` | ⭐⭐⭐⭐⭐ 有原因、有频次、有证据 |
| `evolution_suggestions` | ⭐⭐⭐⭐⭐ 有方向、有证据、有路径 |
| `skill_gaps` | ⭐⭐⭐⭐ 有缺口描述、有优先级 |

### ⚠️ 可优化的字段

| 字段 | 问题 | 建议 |
|-----|------|------|
| `execution_efficiency` | avg_tokens 计算可能有误 (198个session平均应该是 ~14K，不是2.7M) | 修正统计逻辑 |
| `dominant_patterns` | evidence 只保留5个，可能不够 | 可扩展到10个 |

### ❌ 缺失的字段 (可能需要补充)

| 字段 | 缺失原因 |
|-----|---------|
| `confidence_level` | 没有对分析结果的置信度评估 |
| `similar_skill_patterns` | 没有参考其他技能的处理方式 |
| `regression_risks` | 没有修改的风险评估 |

---

## 四、与 ProtoAnalysis 的关系

```
┌─────────────────────────────────────────────────────────────┐
│  批量分析: list[ProtoAnalysis] (198个, 每个~2KB)             │
│                                                              │
│  Stage 5: EvidenceBuilder.build()                            │
│  ├─ 格式化: 证据集概览、状态分布、每个 ProtoAnalysis 的摘要    │
│  └─ 输出: evidence_text.md (198KB)                           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Stage 6: EvidenceAnalyzer.analyze()                         │
│                                                              │
│  输入: evidence_text.md + 198个 CanonicalSession             │
│  LLM分析 → ExecutionAnalysis (8KB)                           │
│                                                              │
│  输出字段:                                                    │
│  ├─ 汇总统计: total, success, failed, rate                   │
│  ├─ 主导模式: dominant_patterns[]                            │
│  ├─ 失败分析: failure_analysis{}                             │
│  ├─ 技能缺口: skill_gaps[]                                   │
│  ├─ 进化建议: evolution_suggestions[] ← 核心输出             │
│  └─ 执行效率: execution_efficiency{}                         │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Stage 7: SkillEvolver.evolve()                              │
│                                                              │
│  输入: evolution_suggestions[] + evidence_session_paths      │
│  ├─ 读取完整会话数据 (通过 LLM 工具)                          │
│  ├─ 生成技能修改补丁                                         │
│  └─ 输出: staging/{skill_name}/changes/                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 五、总结

### 各字段的重要性排序

| 排名 | 字段 | 重要性 | 理由 |
|-----|------|--------|------|
| 1 | `evolution_suggestions` | ⭐⭐⭐⭐⭐ | 进化决策的核心依据 |
| 2 | `failure_analysis.root_causes` | ⭐⭐⭐⭐⭐ | 诊断问题根因 |
| 3 | `skill_gaps` | ⭐⭐⭐⭐ | 识别改进方向 |
| 4 | `dominant_patterns` | ⭐⭐⭐⭐ | 理解执行模式 |
| 5 | `汇总统计` | ⭐⭐⭐ | 提供上下文 |
| 6 | `execution_efficiency` | ⭐⭐ | 性能参考 |

### 对分析 LLM 的评价

| 评估维度 | 评分 | 说明 |
|---------|------|------|
| 分析完整性 | ⭐⭐⭐⭐ | 覆盖了模式、失败、缺口、建议 |
| 证据充分性 | ⭐⭐⭐⭐ | 每个建议都有 session_path 可追溯 |
| 可操作性 | ⭐⭐⭐⭐ | direction 描述具体，可直接指导修改 |
| 分类准确性 | ⭐⭐⭐ | category 和 type 分类合理 |

### 核心结论

1. **ExecutionAnalysis 是 ProtoAnalysis 的高层抽象**:
   - ProtoAnalysis: 单个会话的精简摘要
   - ExecutionAnalysis: 批量会话的分析洞察

2. **evolution_suggestions 是核心输出**:
   - 包含具体的修改方向 (direction)
   - 包含支撑证据 (evidence_session_paths)
   - LLM 可通过 paths 深入查看原始数据

3. **建议增加**:
   - 置信度评估 (confidence_level)
   - 修改风险评估 (regression_risks)