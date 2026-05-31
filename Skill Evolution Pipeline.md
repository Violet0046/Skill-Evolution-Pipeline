# Skill Evolution Pipeline
## Skill 智能进化流水线系统

---

## 目录

1. [架构概览](#1-架构概览)
2. [数据层 (Data)](#2-数据层-data)
3. [提取层 (Extraction)](#3-提取层-extraction)
4. [处理层 (Processing)](#4-处理层-processing)
5. [证据层 (Evidence)](#5-证据层-evidence)
6. [LLM层 (LLM Processing)](#6-llm层-llm-processing)
7. [输出层 (Output)](#7-输出层-output)
8. [决策层 (Decision)](#8-决策层-decision)
9. [完整文件结构](#9-完整文件结构)
10. [开发清单](#10-开发清单)

---

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Skill Evolution Pipeline                      │
│                          Skill 智能进化流水线系统                        │
└─────────────────────────────────────────────────────────────────────────┘
```

## 2. 数据层 (Data)

```
┌─────────────────────────────────────┐
│           数据源                     │
└─────────────────────────────────────┘
                    │
    ┌───────────────┼───────────────┬────────────────┐
    │               │               │                │
    ▼               ▼               ▼                ▼
┌─────────┐     ┌─────────┐   ┌─────────────┐   ┌─────────────┐
│sessions │     │skill.md │   │review_      │   │ configs     │
│execution│     │ configs │   │results/     │   │ .yaml       │
│.jsonl   │     │ (规范)  │   │             │   │             │
│ logs/   │     │         │   │ (审查反馈)  │   │ (配置)      │
│         │     │(当前版本)│   │             │   │             │
│(原始数据)│     │         │   │             │   │             │
│(执行轨迹)│     │         │   │             │   │             │
└────┬────┘     └────┬────┘   └──────┬──────┘   └──────┬──────┘
     │               │               │                 │
     └───────────────┴───────────────┴─────────────────┘
                                    │
```

### 2.1 数据源类型

| 数据类型 | 格式 | 描述 |
|---------|------|------|
| 会话数据 | `.jsonl` | Agent执行轨迹原始数据 |
| Skill配置 | `.md` / `.yaml` | 当前版本技能定义 |
| 审查反馈 | `review_results/` | 审查反馈和评分 |
| 配置文件 | `.yaml` | 流水线配置参数 |

---

## 3. 提取层 (Extraction)

```
┌────────────────┬─────────────────────────────────────────────────┐
│                │                        2. 提取层 (Extraction)   │
└────────────────┴─────────────────────────────────────────────────┘
                                               │
        ┌───────────────────────┬───────────────┴─────────────────┐
        │                       │                                 │
       ▼                       ▼                                 ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────────┐
│SessionExtractor │   │  ReviewParser   │   │   TraceAnalyzer     │
│                 │   │                 │   │                     │
│ 从jsonl提取     │   │ 解析审查反馈    │   │   分析执行轨迹      │
│ 结构化摘要      │   │ 失败原因+建议   │   │   工具调用+效率     │
│                 │   │                 │   │                     │
└────────┬────────┘   └────────┬────────┘   └──────────┬──────────┘
         │                     │                       │
         └─────────────────────┴───────────────────────┘
                               │
                               ▼
              ┌────────────────────────────────────────────┐
              │         标准化中间格式 (Canonical Format)   │
              └────────────────────────────────────────────┘
```

### 3.1 Canonical 格式规范

```json
{
  "session_id": "a01f608112c9c058a",
  "agent_id": "a01f608112c9c058a",
  "skill_name": "协议分析-agent",
  "skill_version": "v1.0",
  "timestamp": "2026-05-13T05:59:57Z",
  "upload_time": "2026-05-21T17:26:47Z",

  "input": {
    "requirement_id": "RAN-1995001",
    "requirement_title": "苹果终端BWP切换机制优化",
    "requirement_type": "功能类",
    "task_description": "协议分析任务",
    "raw_content": "...(原始市场需求内容)"
  },

  "execution": {
    "status": "success|failed",
    "duration_ms": 257000,
    "messages_count": 77,
    "tool_calls": [
      {
        "tool_name": "Bash",
        "call_index": 0,
        "start_time": "...",
        "duration_ms": 1234,
        "success": true
      }
    ],
    "token_usage": {
      "input_tokens": 21529,
      "output_tokens": 220,
      "total": 21749
    }
  },

  "output": {
    "raw_output": "...",
    "structured_result": {
      "protocol_count": 3,
      "protocols": [
        {"name": "TS_38.213", "process": "Bandwidth part operation"},
        {"name": "TS_38.331", "process": "RRC reconfiguration"},
        {"name": "TS_38.214", "process": "Power control"}
      ]
    }
  },

  "feedback": {
    "is_retry": true,
    "retry_reason": "协议记录数量(9)超过规则上限(3)",
    "failure_reason": "规则违反",
    "correction_suggestion": "筛选相关度最高的3条协议记录",
    "quality_score": 15,
    "relevance_level": "直接调用",
    "mentioned_skills": ["协议分析-agent"],
    "is_direct_call": true,
    "problem_type": "rule_violation"
  },

  "domain_specific": {
    "protocols_analyzed": ["TS_38.213", "TS_38.331", "TS_38.214"],
    "output_count": 3,
    "limit_violated": {"max": 3, "actual": 9}
  },

  "metadata": {
    "session_path": "/home/.../agent-a01f608112c9c058a.jsonl",
    "prompt_id": "1e812f44-aac3-4567-8412-0a5b83d1ce6a",
    "is_sidechain": true,
    "entrypoint": "sdk-ts"
  }
}
```

---

## 4. 处理层 (Processing)

```
┌────────────────────────────────────────────────────────────────────────┐
│                       3. 处理层 (Processing)                           │
└────────────────────────────────────────────────────────────────────────┘
                                               │
        ┌───────────────────────┬───────────────┴─────────────────┐
        │                       │                                 │
       ▼                       ▼                                 ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────────┐
│  QualityFilter  │   │  Cluster Engine │   │  Pattern Extractor  │
│                 │   │                 │   │                     │
│ ┌─────────────┐ │   │ ┌─────────────┐ │   │ ┌─────────────────┐ │
│ │ 规则过滤器  │ │   │ │多样性采样  │ │   │ │ ┌─────────────┐ │ │
│ │ • 质量分数 │ │   │ │ 相似度计算  │ │   │ │ │失败模式提取│ │ │
│ │ • 完整性   │ │   │ │ • 覆盖度   │ │   │ │ │  • 规则违反 │ │ │
│ │ • 时效性   │ │   │ │ • 语义相似度│ │   │ │ │  • 质量问题 │ │ │
│ └─────────────┘ │   │ │ • 均衡性   │ │   │ │ └─────────────┘ │ │
│                 │   │ │ • 关键词匹配│ │   │ │                 │ │
│ ┌─────────────┐ │   │ │ • 代表性   │ │   │ │ ┌─────────────┐ │ │
│ │ 类型分组    │ │   │ └─────────────┘ │   │ │ │ 最佳实践提取│ │ │
│ │ • 重试案例 │ │   │                 │   │ │ │  • 成功要素 │ │ │
│ │ • 失败案例 │ │   │ ┌─────────────┐ │   │ │ │  • 高效流程 │ │ │
│ │ • 成功案例 │ │   │ │ 聚类去重    │ │   │ │ │  • 工具使用 │ │ │
│ │ • 复杂场景 │ │   │ │ 分层抽样   │ │   │ │ └─────────────┘ │ │
│ └─────────────┘ │   │ │ • SimHash   │ │   │ └─────────────────┘ │
│                 │   │ │ • 向量聚类  │ │   │                     │
│                 │   │ │ • 中心选取  │ │   │                     │
│                 │   │ └─────────────┘ │   │                     │
└────────┬────────┘   └────────┬────────┘   └──────────┬──────────┘
         │                     │                       │
         └─────────────────────┴───────────────────────┘
                               │
                               ▼
              ┌────────────────────────────────────────────┐
              │         4. 证据层 (Evidence)                │
              └────────────────────────────────────────────┘
```

### 4.1 处理组件说明

#### QualityFilter (质量过滤器)
- **质量分数过滤**: 剔除评分过低的样本
- **完整性检查**: 确保必要字段完整
- **时效性过滤**: 排除过期数据
- **类型分组**: 按执行结果分类

#### ClusterEngine (聚类引擎)
- **多样性采样**: 覆盖不同场景
- **相似度计算**: 语义 + 关键词双层匹配
- **聚类去重**: SimHash + 向量聚类

#### PatternExtractor (模式提取器)
- **失败模式**: 识别高频失败原因
- **成功要素**: 归纳成功关键点
- **优化方向**: 提出改进建议

---

## 5. 证据层 (Evidence)

```
┌────────────────────────────────────────────────────────────────────────┐
│                           4. 证据层 (Evidence)                          │
└────────────────────────────────────────────────────────────────────────┘
                                               │
        ┌───────────────────────┬───────────────┴─────────────────┐
        │                       │                                 │
       ▼                       ▼                                 ▼
┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│   evidence_set/     │   │     test_set/       │   │     insights/       │
│                     │   │                     │   │                     │
│ ┌─────────────────┐ │   │ ┌─────────────────┐ │   │ ┌─────────────────┐ │
│ │  layer1_raw/    │ │   │ │ problem_cases/  │ │   │ │failure_patterns │ │
│ │                 │ │   │ │                 │ │   │ │      .md        │ │
│ │ • summaries/    │ │   │ │ • case_001.json │ │   │ └─────────────────┘ │
│ │   session_001   │ │   │ │ • case_002.json │ │   │ ┌─────────────────┐ │
│ │   _summary.json │ │   │ │ • ...          │ │   │ │best_practices.md│ │
│ │   ...           │ │   │ └─────────────────┘ │   │ └─────────────────┘ │
│ │                 │ │   │                     │   │ ┌─────────────────┐ │
│ │                 │ │   │ ┌─────────────────┐ │   │ │optimization_    │ │
│ └─────────────────┘ │   │ │ ground_truth/   │ │   │ │    plan.md      │ │
│                     │   │ │                 │ │   │ └─────────────────┘ │
│ ┌─────────────────┐ │   │ │ • case_001.json │ │   │ ┌─────────────────┐ │
│ │layer2_clustered/│ │   │ │ • case_002.json │ │   │ │evolution_       │ │
│ │                 │ │   │ │ • ...          │ │   │ │    report.md    │ │
│ │ • retry_cases/  │ │   │ └─────────────────┘ │   │ └─────────────────┘ │
│ │ • failed_cases/ │ │   │                     │   └─────────────────────┘
│ │ • success_cases/│ │   │ ┌─────────────────┐ │
│ └─────────────────┘ │   │ │execution_      │ │
│                     │   │ │    results/    │ │
│ ┌─────────────────┐ │   │ │               │ │
│ │layer3_selected/ │ │   │ │ ┌───────────┐ │ │
│ │                 │ │   │ │ │  old/     │ │ │
│ │ • diverse_      │ │   │ │ │  • ...    │ │ │
│ │   samples.json  │ │   │ │ ├───────────┤ │ │
│ │ • selected_     │ │   │ │ │  new/     │ │ │
│ │   _10.json      │ │   │ │ │  • ...    │ │ │
│ │ • deep_         │ │   │ │ └───────────┘ │ │
│ │   analysis.md   │ │   │ └─────────────────┘ │
│ └─────────────────┘ │   │                      │
└─────────────────────┘   └──────────────────────┘
```

### 5.1 数据规模

| 目录 | 原始大小 | 压缩后 |
|------|---------|--------|
| evidence_set/ | ~80MB | ~1MB |
| test_set/ | ~10MB | ~500KB |

### 5.2 证据层级说明

| 层级 | 内容 | 说明 |
|------|------|------|
| layer1_raw | summaries/ | 原始数据提取后的结构化摘要 |
| layer2_clustered | retry/failed/success_cases/ | 按类型聚类后的分组 |
| layer3_selected | diverse_samples, selected_N | 多样性采样精选的代表性样本 |

---

## 6. LLM层 (LLM Processing)

```
┌────────────────────────────────────────────────────────────────────────┐
│                      5. LLM层 (LLM Processing)                         │
└────────────────────────────────────────────────────────────────────────┘
                                               │
        ┌───────────────────────┬───────────────┴─────────────────┐
        │                       │                                 │
       ▼                       ▼                                 ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────────┐
│  BatchAnalyzer  │   │  DeepAnalyzer   │   │   SkillOptimizer    │
│ (轻量LLM x N)   │   │ (强LLM x 1)     │   │    (强LLM x 1)      │
│                 │   │                 │   │    (规则+LLM)       │
└────────┬────────┘   └────────┬────────┘   └──────────┬──────────┘
         │                     │                       │
         ▼                     ▼                       ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     LLM 处理流程说明                                    │
└────────────────────────────────────────────────────────────────────────┘
```

### 6.1 BatchAnalyzer (批量分析器)

**输入:**
- 聚类后的同类Case
- 模式统计

**处理:**
- 并行分析
- 批量统计
- 模式归纳

**输出:**
- 失败模式列表
- 成功要素列表
- 高频Case识别结果

### 6.2 DeepAnalyzer (深度分析器)

**输入:**
- 10个精选样本
- 完整对话轨迹
- 执行结果

**处理:**
- 逐个深度分析
- 失败根因挖掘
- 质量评估
- 效率分析
- 改进点识别
- 稳定性检查

**输出:**
- 深度分析报告
- 各维度得分
- 关键洞察
- 综合评分
- 优化方向

### 6.3 SkillOptimizer (Skill优化器)

**输入:**
- 当前skill.md
- 优化建议
- 变更日志

**处理:**
- 生成优化建议
- 逐条优化
- 版本对比

**输出:**
- skill_v2.md (新版本)
- 变更说明
- 回滚方案

---

## 7. 输出层 (Output)

```
┌────────────────────────────────────────────────────────────────────────┐
│                            6. 输出层 (Output)                           │
└────────────────────────────────────────────────────────────────────────┘
                                               │
        ┌───────────────────────────────────────┼───────────────────────┐
        │                                       │                       │
       ▼                                       ▼                       ▼
┌────────────────────────────┐   ┌───────────────────────────┐   ┌──────────────────────┐
│    skill.md (当前版本)      │   │    skill_v2.md (新版本)    │   │  evaluation_report.md│
│                             │   │                           │   │                      │
│ # 协议分析-agent             │   │ # 协议分析-agent (v2.1)    │   │  # 评估报告          │
│                             │   │                           │   │  生成时间: 2026-05-29│
│ ## 0. 开始时间上报           │   │ ## 0. 开始时间上报         │   │  对比版本: v2.0 vs v2│
│ ## 1. 协议分析               │   │ ## 1. 协议分析 [优化]      │   │                      │
│     1.1 任务输入             │   │     1.1 任务输入           │   │  ## 测试统计         │
│     1.2 任务步骤             │   │     1.2 任务步骤           │   │  | 维度    |旧版本  ||
│         步骤1: 获取协议列表  │   │         步骤1: 获取协议列表│   │                      │
│         步骤2: 分析波及协议  │   │         步骤2: 分析波及协议│   │  ## 综合评分         │
│         步骤3: 获取协议内容  │   │         步骤3: 获取协议内容│   │  - 旧版本平均分: 72.3│
│         步骤4: 分析协议过程  │   │           [增加数量限制检查]│  │  - 新版本平均分: 85.6│
│         步骤5: 提取信令信元  │   │         步骤4: 分析协议过程│   │  - 提升幅度: +13.3   │
│     1.3 输出要求             │   │           [增加相关性排序] │   │                      │
│ ## 2. 任务输出               │   │         步骤5: 提取信令信元│   │  ## 各维度得分       │
│ ## 3. 结束时间上报           │   │           [数量<=3限制]    │   │  ...                 │
│                             │   │     1.3 输出要求           │   │                      │
│                             │   │        [增加格式校验]      │   │  ## 决策建议         │
│                             │   │ ## 2. 任务输出             │   │  **决策**: ✅ APPROVE│
│                             │   │ ## 3. 结束时间上报         │   │                      │
│                             │   │                           │   │  ## 风险提示         │
│                             │   │ ## 变更日志                │   │  ...                 │
│                             │   │ v2.1:                     │   └──────────────────────┘
│                             │   │ - 步骤2增加数量限制检查    │
│                             │   │ - 步骤4增加相关性排序      │
│                             │   │ - 步骤5增加数量<=3限制     │
└─────────────────────────────┘   └───────────────────────────┘
```

### 7.1 评估报告模板

```markdown
# 评估报告

生成时间: 2026-05-29
对比版本: v2.0 vs v2.1

## 测试统计

| 维度          | 旧版本    | 新版本    | 变化     |
|---------------|-----------|-----------|----------|
| 测试用例数    | 50        | 50        | -        |
| 通过数        | 35        | 48        | +13      |
| 通过率        | 70.0%     | 96.0%     | +26.0%   |

## 综合评分

- 旧版本平均分: 72.3
- 新版本平均分: 85.6
- 提升幅度: +13.3 (+18.4%)

## 各维度得分

| 维度           | 权重 | 旧版本 | 新版本 | 变化 |
|----------------|------|--------|--------|------|
| rule_compliance | 30%  | 68     | 92     | +24  |
| output_quality  | 30%  | 75     | 82     | +7   |
| efficiency      | 20%  | 78     | 80     | +2   |
| stability       | 20%  | 68     | 88     | +20  |

## 典型案例对比

- case_001: 规则违反 → 通过 (+25分)
- case_002: 质量问题 → 通过 (+15分)
- ...

## 决策建议

**综合评分**: 85.6
**改进幅度**: +18.4%
**决策**: ✅ APPROVE
**理由**: 改进幅度超过阈值(10%)，建议合并

## 风险提示

- 新版本可能在简单场景下过于严格
- 建议观察上线后3天的表现
```

---

## 8. 决策层 (Decision)

```
┌────────────────────────────────────────────────────────────────────────┐
│                          7. 决策层 (Decision)                           │
└────────────────────────────────────────────────────────────────────────┘
                                               │
        ┌───────────────┼───────────────┐
        │               │               │
       ▼               ▼               ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  APPROVE ✓  │   │NEED_REVIEW  │   │  REJECT ✗   │
│             │   │             │   │             │
│ 合并条件:   │   │ 合并条件:   │   │ 回滚条件:   │
│ 改进>=15%   │   │ 0%<=改进    │   │ 改进<-5%    │
│             │   │   <15%      │   │             │
├─────────────┤   ├─────────────┤   ├─────────────┤
│ 自动操作:   │   │ 人工操作:   │   │ 保留旧版本  │
│ • 合并版本  │   │ • 通知审核  │   │ • 记录原因  │
│ • 更新日志  │   │ • 等待确认  │   │ • 分析改进  │
│ • 通知相关  │   │ • 执行合并  │   │ • 下次迭代  │
└─────────────┘   └─────────────┘   └─────────────┘
```

### 8.1 决策规则

| 决策 | 条件 | 操作 |
|------|------|------|
| APPROVE | 改进 >= 15% | 自动合并版本、更新日志、通知相关人 |
| NEED_REVIEW | 0% <= 改进 < 15% | 通知审核、等待确认、执行合并 |
| REJECT | 改进 < -5% | 保留旧版本、记录原因、分析改进方向 |

---

## 9. 完整文件结构

```
skill-evolution-pipeline/
│
├── 📂 src/
│   │
│   ├── 📂 models/                    # 数据模型
│   │   ├── __init__.py
│   │   ├── session.py                # Session相关数据类
│   │   ├── test_case.py              # 测试用例数据类
│   │   ├── evaluation.py             # 评估相关数据类
│   │   └── skill.py                  # Skill相关数据类
│   │
│   ├── 📂 config/                    # 配置管理
│   │   ├── __init__.py
│   │   ├── settings.py               # 配置类定义
│   │   └── prompts.py                # LLM提示词模板
│   │
│   ├── 📂 extraction/                # 提取层
│   │   ├── __init__.py
│   │   ├── session_extractor.py      # Session提取器
│   │   ├── review_parser.py          # 审查反馈解析器
│   │   └── trace_analyzer.py         # 执行轨迹分析器
│   │
│   ├── 📂 processing/                # 处理层
│   │   ├── __init__.py
│   │   ├── quality_filter.py         # 质量过滤器
│   │   ├── cluster_engine.py         # 聚类引擎
│   │   ├── pattern_extractor.py      # 模式提取器
│   │   └── sampler.py                # 采样器
│   │
│   ├── 📂 llm/                       # LLM处理层
│   │   ├── __init__.py
│   │   ├── batch_analyzer.py         # 批量分析器
│   │   ├── deep_analyzer.py          # 深度分析器
│   │   ├── skill_optimizer.py        # Skill优化器
│   │   └── llm_client.py             # LLM客户端
│   │
│   ├── 📂 evaluation/                # 评估层
│   │   ├── __init__.py
│   │   ├── evaluator.py              # 评估器
│   │   ├── metrics.py                # 评估指标
│   │   └── decision_engine.py        # 决策引擎
│   │
│   ├── 📂 execution/                 # 执行层
│   │   ├── __init__.py
│   │   ├── skill_executor.py         # Skill执行器
│   │   └── result_collector.py       # 结果收集器
│   │
│   └── 📂 pipeline/                  # 流水线
│       ├── __init__.py
│       ├── main.py                   # 主入口
│       ├── orchestrator.py           # 编排器
│       └── utils.py                  # 工具函数
│
├── 📂 configs/
│   ├── default.yaml                  # 默认配置
│   ├── protocols.yaml                # 协议分析-agent配置
│   └── prompts/                      # 提示词模板
│       ├── batch_analysis_prompt.txt
│       ├── deep_analysis_prompt.txt
│       └── optimization_prompt.txt
│
├── 📂 data/                          # 数据目录
│   │
│   ├── 📂 protocol-agent/            # 协议分析-agent
│   │   ├── skill.md                  # 当前版本
│   │   ├── sessions.jsonl            # 原始会话数据
│   │   │
│   │   ├── 📂 evidence_set/
│   │   │   ├── layer1_raw/
│   │   │   ├── layer2_clustered/
│   │   │   └── layer3_selected/
│   │   │
│   │   ├── 📂 test_set/
│   │   │   ├── problem_cases/
│   │   │   ├── ground_truth/
│   │   │   └── evaluation_results/
│   │   │
│   │   └── 📂 evolution_logs/
│   │       ├── v1_to_v2.log
│   │       └── v2_to_v3.log
│   │
│   └── 📂 other-agents/              # 其他Agent...
│
├── 📂 output/                        # 流水线输出
│   ├── 📂 runs/
│   │   └── run_20260529_143000/
│   │       ├── evidence_set/         # 本次生成的证据集
│   │       ├── test_set/             # 本次生成的测试集
│   │       ├── insights/             # 分析洞察
│   │       ├── skill_v2.md           # 优化后的版本
│   │       └── evaluation_report.md  # 评估报告
│   │
│   └── 📂 reports/                   # 历史报告
│
├── 📂 tests/                         # 单元测试
│   ├── test_extraction.py
│   ├── test_processing.py
│   ├── test_evaluation.py
│   └── test_integration.py
│
├── 📂 scripts/                       # 脚本
│   ├── run_pipeline.py               # 运行流水线
│   ├── generate_test_set.py          # 生成测试集
│   └── analyze_results.py            # 分析结果
│
├── pyproject.toml
├── README.md
└── requirements.txt
```

---

## 10. 开发清单

### 阶段1：基础设施 (Foundation)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 1.1 | models | session.py | CanonicalSession, ToolCall, TokenUsage | P0 |
| 1.2 | models | test_case.py | TestCase, ExecutionRecord, EvaluationCriteria | P0 |
| 1.3 | models | evaluation.py | EvaluationReport, DecisionResult, DimensionScore | P0 |
| 1.4 | models | skill.py | SkillChange, EvolutionLog | P1 |
| 1.5 | config | settings.py | PipelineConfig, DataConfig, LLMConfig | P0 |
| 1.6 | config | prompts.py | LLM提示词模板定义 | P1 |
| 1.7 | llm | llm_client.py | LLM调用封装 | P0 |

### 阶段2：提取层 (Extraction)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 2.1 | extraction | session_extractor.py | jsonl → CanonicalSession | P0 |
| 2.2 | extraction | review_parser.py | 解析审查反馈 | P1 |
| 2.3 | extraction | trace_analyzer.py | 分析执行轨迹 | P2 |

### 阶段3：处理层 (Processing)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 3.1 | processing | quality_filter.py | 质量筛选 | P0 |
| 3.2 | processing | cluster_engine.py | 相似度聚类 | P1 |
| 3.3 | processing | pattern_extractor.py | 模式提取 | P1 |
| 3.4 | processing | sampler.py | 多样性采样 | P0 |

### 阶段4：LLM处理 (LLM Processing)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 4.1 | llm | batch_analyzer.py | 批量模式分析 | P1 |
| 4.2 | llm | deep_analyzer.py | 深度洞察分析 | P1 |
| 4.3 | llm | skill_optimizer.py | 生成优化建议 | P0 |

### 阶段5：评估层 (Evaluation)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 5.1 | evaluation | metrics.py | 评估指标计算 | P0 |
| 5.2 | evaluation | evaluator.py | 多维度评估 | P0 |
| 5.3 | evaluation | decision_engine.py | 决策逻辑 | P0 |

### 阶段6：执行层 (Execution)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 6.1 | execution | skill_executor.py | 执行Skill | P2 |
| 6.2 | execution | result_collector.py | 收集执行结果 | P2 |

### 阶段7：流水线编排 (Orchestration)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 7.1 | pipeline | orchestrator.py | 流水线编排 | P0 |
| 7.2 | pipeline | main.py | 主入口 | P0 |

### 阶段8：工具与测试 (Utils & Tests)

| 序号 | 模块 | 文件 | 功能 | 优先级 |
|------|------|------|------|--------|
| 8.1 | pipeline | utils.py | 工具函数 | P1 |
| 8.2 | tests | test_*.py | 单元测试 | P1 |
| 8.3 | scripts | run_pipeline.py | 运行脚本 | P1 |

---

## 附录：优先级说明

| 优先级 | 说明 |
|--------|------|
| P0 | 核心功能，必须优先完成 |
| P1 | 重要功能，第二个迭代完成 |
| P2 | 扩展功能，可后续实现 |

---

*文档版本: v1.0*
*最后更新: 2026-05-29*



N 个 JSONL 文件
    │
    ▼ 【代码】ProtoExtractor（新增）
N 个 ProtoAnalysis（~500B/session）
    │
    ▼ 【代码】QualityFilter + Split（已有）
进化集 + 测试集
    │
    ▼ 【代码】EvidenceBuilder（新增）
把进化集格式化成一个文本块（参考 OpenSpace 的 _format_analysis_context）
    │
    ▼ 【LLM】EvidenceAnalyzer（新增，参考 OpenSpace 的 analyzer）
一个 LLM 调用，看到全部证据 → 输出 ExecutionAnalysis
（包含 failure_patterns, success_factors, evolution_suggestions）
    │
    ▼ 【LLM】SkillEvolver（后续阶段，参考 OpenSpace 的 evolver）
基于 ExecutionAnalysis → 生成新版本 skill

Step 1: ProtoExtractor（代码）

输入: agent-a01f...jsonl (150KB)
输出: ProtoAnalysis (~500B)

@dataclass
class ProtoAnalysis:
    session_id: str
    status: str                    # success / retry_success / failed
    task_title: str                # "苹果终端BWP切换机制优化"
    task_description: str          # 任务描述
    tool_sequence: str             # "Read→Bash→Read→Bash→Write"
    failure_reason: str            # "协议记录数量(9条)超过规则上限(3条)"
    correction: str                # "筛选相关度最高的3条协议记录"
    final_output: str              # 最后一条 assistant 消息的前 500 字符
    error_tool_calls: list[str]    # 出错的工具调用摘要
    token_usage: int               # 总 token 消耗
    duration_seconds: float
纯代码，正则提取，零 LLM 成本。

Step 2: QualityFilter + Split（已有）
过滤低质量 proto-analysis，拆分进化集/测试集。已有代码，不用改。

Step 3: EvidenceBuilder（新增）
把 N 个 ProtoAnalysis 格式化成一个文本块，参考 OpenSpace 的 _format_analysis_context()：


# Skill Evolution Evidence

## Current Skill
{skill_content}

## Execution Evidence ({N} sessions)

### Session 1 (retry_success)
Task: 苹果终端BWP切换机制优化
Failure: 协议记录数量(9条)超过规则上限(3条)
Correction: 筛选相关度最高的3条协议记录
Tools: Read→Bash→Read→Bash→Read→Read→Read→Bash→Read→Write→...
Output: | TS_38.213 | Bandwidth part operation | BWP-Id |...

### Session 2 (success)
Task: 苹果终端BWP切换机制优化
Tools: Bash→Read→Read→Bash→Read→Bash→Bash→Bash→Read→...
Output: | TS_38.331 | RRC reconfiguration | BWP-Configuration |...

...
纯代码，字符串拼接。

Step 4: EvidenceAnalyzer（LLM，参考 OpenSpace）
一个 LLM 调用，输入是 Step 3 的文本块，输出参考 OpenSpace 的 ExecutionAnalysis：


{
  "task_completed": true,
  "execution_note": "10个session中，3个因协议记录数量超限失败，7个成功。核心问题是skill步骤2和5缺乏数量硬约束。",
  "failure_patterns": [
    "步骤2: 未约束协议册数量，导致输出超过3条",
    "步骤5: 信令信元未按相关度筛选"
  ],
  "success_factors": [
    "重试时明确给出'最多3条'约束后可通过"
  ],
  "evolution_suggestions": [
    {
      "type": "fix",
      "direction": "在步骤2和步骤5中增加明确的数量硬约束：输出数量必须<=3，否则视为失败"
    }
  ],
  "interesting_sessions": ["f0b21ecb-..."]
}
如果 LLM 觉得某个 session 需要深入看 → 用 read_file 读原始 JSONL。

Execute() → [分析LLM] → ExecutionAnalysis（含 evolution_suggestions）
                              │
                              ▼
                    [进化LLM] → 新 skill

N 个 session → [代码] → N 个 ProtoAnalysis
                            │
                            ▼
                    EvidenceBuilder → 文本块
                            │
                            ▼
                    [分析LLM] → 综合 Analysis（看全貌、找模式）
                            │
                            ▼
                    [进化LLM] → 新 skill


                    OpenSpace 的流程：

1 次执行 → 1 个 ExecutionAnalysis → N 个 suggestions → 串行处理
suggestions 之间可能有关联（比如 "修复 X" 和 "增强 Y"），但 OpenSpace 不做去重，按顺序逐个 apply
我们的优势：

N 个 session → 1 个聚合 ExecutionAnalysis → M 个 suggestions → 串行处理
因为看到了多个 session 的证据，同一个问题可能被多个 session 反复暴露，比如 session 1 失败原因是"协议数量超限"，session 2 也是 — EvidenceAnalyzer 可以把它们合并成一条 suggestion，天然去重
所以我们的 suggestions 质量会更高：频次感知 + 跨 session 去重，这恰恰是多 session 聚合分析的核心价值。   







在项目根目录 Self-Evolution-Pipeline/ 下执行：


# 设置环境变量（你的 API 凭据）
set ANTHROPIC_API_KEY=tp-cm02skhdu7z6yw1hr1cs3dj67hj02l9t55phymomh4be138z
set ANTHROPIC_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic

# 跑完整流水线
python -m "Skill Evolution Pipeline.src.pipeline.main" --stage all
输出目录在 Skill Evolution Pipeline/output/runs/run_YYYYMMDD_HHMMSS/。

7 个阶段 + 对应产物
阶段	做什么	产物文件	怎么看
1. Extract	JSONL → CanonicalSession	无独立文件，内存中	终端打印 session_id, status, tokens
2. Filter	按 quality_score 过滤	无	终端打印 passed/discarded
3. Split	70/30 拆分	evolution_evidence.json test_set.json run_meta.json	看 run_meta.json 的统计数字
4. ProtoExtract	Session → ProtoAnalysis	无独立文件	终端打印 tool_sequence
5. EvidenceBuild	N 个 ProtoAnalysis → 文本块	evidence_text.json	看 evidence_text 字段，约 1.5KB
6. Analyze	LLM 分析证据集	execution_analysis.json	核心产物，看 evolution_suggestions
7. Evolve	LLM 逐个处理 suggestions	evolution_results.json + evolved_skills/ 目录	看每个 suggestion 的 ok/error
重点看什么
execution_analysis.json — 最重要的中间产物：


evolution_suggestions[0].type        → "fix" 或 "derived"
evolution_suggestions[0].direction   → LLM 给出的具体改进方向
failure_analysis.root_causes[0]     → 根本原因
skill_gaps[0]                       → 技能缺口
evolution_results.json — 进化结果：


results[0].ok               → 是否成功 apply
results[0].change_summary   → LLM 生成的变更摘要
results[0].error            → 失败原因（当前是 "SKILL.md not found"）
evolved_skills/ — 如果 apply 成功，这里会有新生成的 SKILL.md 文件。

当前的预期行为
进化阶段的 2 条 suggestion 会报错 SKILL.md not found，因为我们没有真实的协议分析 Skill 文件。这是正常的 — 流水线的代码逻辑是完整的，只是缺少输入的 Skill 内容。


执行 python -m "Skill Evolution Pipeline.src.pipeline.main"流程
============================================================
Skill Evolution Pipeline
============================================================
Skill:       protocol-agent
Project:     D:\VS\26project\Self-Evolution-Pipeline
Output:      D:\VS\26project\Self-Evolution-Pipeline\Skill Evolution Pipeline\output\runs\run_20260531_174248
Stage:       all
============================================================
  Loaded 272 index entries from sessions.jsonl
[EXTRACT] Found 2 JSONL file(s)
  Processing: agent-a01f608112c9c058a.jsonl
    -> session_id=f0b21ecb-3710-4c5f-bef7-6cad3247aa27
       status=retry_success, messages=77, tools=24, tokens=1065668
       quality_score=60, retry=True
  Processing: agent-aec40353c470e2cb8.jsonl
    -> session_id=5269b536-fce3-4b7b-b44c-16e385b6d26a
       status=success, messages=61, tools=20, tokens=550748
       quality_score=39, retry=True

[FILTER] Results:
  Input: 2 sessions
  Passed: 2
    failed: 0
    retry_success: 1
    success: 1

[SPLIT] Results:
  Evolution set: 2 sessions
    - f0b21ecb-371... (retry_success)
    - 5269b536-fce... (success)
  Test set: 0 sessions

[PROTO] Extracted 2 ProtoAnalyses
  - f0b21ecb-371: retry_success, tools=Skill→Read→Bash→Read→Bash→Read→Bash→Read
  - 5269b536-fce: success, tools=Skill→Bash→Read→Bash→Read→Bash→Read→Bash

[EVIDENCE] Built evidence text: 1585 chars

[ANALYSIS] LLM analysis complete:
ExecutionAnalysis: protocol-agent
  Sessions: 2 (success=1, retry=1, failed=0)
  Success rate: 100%
  Patterns: 1
  Root causes: 1
  Skill gaps: 1
  Evolution suggestions: 2
    [fix] 在协议分析流程中添加前置验证步骤：自动检查协议记录数量，如果超过3条，则基于相关性筛选最高优先级的记录，确保输出符合规则上限
    [derived] 增强协议记录筛选逻辑，使其能根据任务上下文自动评估相关性，并支持动态调整筛选策略，以泛化到不同协议分析场景

[EVOLVE] Processing 2 suggestion(s)

  Suggestion 1/2: [fix] 在协议分析流程中添加前置验证步骤：自动检查协议记录数量，如果超过3条，则基于相关性筛选最高优先级的记录，确保输出符合规则...
    OK: Added automatic filtering of protocol records based on relevance when exceeding 3 entries, replacing the previous blocking validation.

  Suggestion 2/2: [derived] 增强协议记录筛选逻辑，使其能根据任务上下文自动评估相关性，并支持动态调整筛选策略，以泛化到不同协议分析场景...
    OK: 增强协议筛选逻辑，通过分析需求文档自动评估协议相关性并支持动态策略选择，以提升分析的准确性和泛化能力。

[EVOLVE] Results: 2 ok, 0 failed
  [OK] fix: Added automatic filtering of protocol records based on relevance when exceeding 3 entries, replacing the previous blocking validation.
  [OK] derived: 增强协议筛选逻辑，通过分析需求文档自动评估协议相关性并支持动态策略选择，以提升分析的准确性和泛化能力。

  Analysis output:
    D:\VS\26project\Self-Evolution-Pipeline\Skill Evolution Pipeline\output\runs\run_20260531_174248\evidence_text.json
    D:\VS\26project\Self-Evolution-Pipeline\Skill Evolution Pipeline\output\runs\run_20260531_174248\execution_analysis.json
    D:\VS\26project\Self-Evolution-Pipeline\Skill Evolution Pipeline\output\runs\run_20260531_174248\evolution_results.json

  Output files:
    D:\VS\26project\Self-Evolution-Pipeline\Skill Evolution Pipeline\output\runs\run_20260531_174248\evolution_evidence.json
    D:\VS\26project\Self-Evolution-Pipeline\Skill Evolution Pipeline\output\runs\run_20260531_174248\test_set.json
    D:\VS\26project\Self-Evolution-Pipeline\Skill Evolution Pipeline\output\runs\run_20260531_174248\run_meta.json

============================================================
Pipeline complete.
============================================================
(base) PS D:\VS\26project\Self-Evolution-Pipeline> 


