# ProtoAnalysis 字段分析报告

## 原始数据

- **CanonicalSession 大小**: 111KB
- **ProtoAnalysis 大小**: 1.8KB
- **压缩比**: 98.4%

---

## 一、各字段作用详解

### 1. 核心标识字段

| 字段 | 当前值示例 | 作用 | 建议 |
|-----|-----------|------|------|
| `session_id` | `f0b21ecb-3710-4c5f-bef7-6cad3247aa27` | 唯一标识会话 | 保留，但LLM分析时只用到前12位即可 |
| `status` | `retry_success` | 执行结果状态：success/failed/retry_success | **必须保留**，核心判断依据 |

### 2. 任务描述字段

| 字段 | 当前值示例 | 作用 | 建议 |
|-----|-----------|------|------|
| `task_title` | `苹果终端BWP切换机制优化` | 任务标题 | **必须保留**，帮助LLM理解任务上下文 |
| `task_description` | `协议分析（重试）` | 任务描述 | 可选保留，与title有轻微重叠 |

### 3. Agent 行为字段（核心分析数据）

| 字段 | 当前值示例 | 作用 | 建议 |
|-----|-----------|------|------|
| `tool_sequence` | `Skill→Read→Bash→Read→...→Skill` | 去重后的工具调用序列（21个） | **必须保留**，反映Agent执行路径 |
| `key_tools` | `["Skill", "Read", "Bash", "Write", "Edit"]` | 去重后的工具名称列表（5个） | 可选保留，与tool_sequence功能重叠 |

### 4. 失败分析字段（进化关键数据）

| 字段 | 当前值示例 | 作用 | 建议 |
|-----|-----------|------|------|
| `failure_reason` | `协议记录数量(9条)超过规则上限(3条)` | 失败原因描述 | **必须保留**，诊断信息 |
| `correction` | `筛选相关度最高的3条协议记录` | 修正建议 | **必须保留**，进化方向参考 |

### 5. 执行结果字段

| 字段 | 当前值示例 | 作用 | 建议 |
|-----|-----------|------|------|
| `final_output` | `## 协议分析任务执行完成...`（300字符） | 最后一条assistant消息的前300字符 | **需要扩充**，当前严重不足 |
| `error_tool_calls` | `["Write: {...}", "Edit: {...}"]` | 包含错误的工具调用列表 | 可选保留，最多3条即可 |

### 6. 统计/元数字段

| 字段 | 当前值 | 作用 | 建议 |
|-----|-------|------|------|
| `token_usage` | 1065668 | 消耗的总token数 | 可删除，纯元数据 |
| `duration_seconds` | 256.824 | 执行时长 | 可删除，纯元数据 |
| `message_count` | 77 | 消息总数 | 可删除，冗余统计 |
| `tool_call_count` | 24 | 工具调用次数 | 可删除，冗余统计 |

### 7. 质量评估字段

| 字段 | 当前值 | 作用 | 建议 |
|-----|-------|------|------|
| `quality_score` | 0 | 质量评分 | 可选保留，但需确保有实际值 |
| `relevance_level` | 空 | 相关性等级 | 可选保留，但需确保有实际值 |

### 8. 文件路径字段

| 字段 | 当前值 | 作用 | 建议 |
|-----|-------|------|------|
| `source_file` | 空 | 原始JSONL文件路径 | **删除**，无分析价值 |
| `session_path` | 空 | 指向完整 session JSONL 的路径 | **保留** ⚡ LLM 可按需用工具读取完整数据 |

**session_path 的价值**:
```
LLM 分析 ProtoAnalysis 后认为"这个会话值得深入看"
    ↓
LLM 调用 Read(session_path) 
    ↓
获得完整 CanonicalSession (77条消息 + 原始输入 + 技能规范等)
    ↓
进行深度分析
```

这样既保持了 ProtoAnalysis 的精简，又保留了按需查看完整数据的能力。

---

## 二、对分析 LLM 的充分性评估

### ✅ 足够的信息（保留）

| 字段 | 理由 |
|-----|------|
| `status` | 判断成败的基础，必须有 |
| `failure_reason` | 失败诊断的核心，必须有 |
| `correction` | 修正方向参考，必须有 |
| `tool_sequence` | Agent行为模式反映，必须有 |
| `task_title` | 任务上下文，短文本开销低 |

### ⚠️ 信息不足（需要补充）

| 缺失内容 | 问题描述 | 建议 |
|---------|---------|------|
| **技能规范摘要** | 没有保留技能执行规范（如协议分析skill的步骤要求） | 新增 `skill_constraints` 字段，提取前500字符 |
| **关键步骤标记** | 没有记录是否完成关键操作（读取输入、调用脚本等） | 新增 `completion_markers` 字段，boolean列表 |
| **中间结果摘要** | 没有提取关键中间结果（如返回的协议册列表） | 新增 `key_intermediate_results` 字段，最多3条 |
| **final_output 长度** | 300字符对复杂任务严重不足 | 扩充到 800 字符 |

### ❌ 冗余信息（建议删除）

| 字段 | 问题 | 理由 |
|-----|------|------|
| `message_count` | 纯统计值 | tool_sequence 已隐含执行复杂度 |
| `tool_call_count` | 纯统计值 | tool_sequence 已隐含执行复杂度 |
| `source_file` | 文件路径 | 无分析价值 |
| `duration_seconds` | 元数据 | 不影响进化决策 |
| `token_usage` | 元数据 | 不影响进化决策 |

### ✅ 意外发现的价值字段

| 字段 | 原以为 | 实际价值 |
|-----|-------|---------|
| `session_path` | 文件路径，无用 | **重要** - LLM 可按需读取完整 CanonicalSession |

---

## 三、建议的改进方案

### 立即改进（必须）

| 改动 | 说明 |
|-----|------|
| `final_output` 从 300 → 800 字符 | 复杂任务（如协议分析）输出远超300字符，当前截断丢失关键结果 |

### 短期改进（强烈建议）

| 新增字段 | 来源 | 长度 | 说明 |
|---------|------|------|------|
| `skill_constraints` | `session.task_input.skill_content` | 500字符 | 技能规范的核心约束，用于判断Agent是否正确执行 |
| `completion_markers` | 从消息中提取 | 列表 | 标记关键步骤：read_input、called_script、generated_output |
| `key_intermediate_results` | 从tool_result中提取 | 最多3条 | 关键中间结果，如协议册列表、输出文件路径等 |

### 中期改进（可选）

| 删除字段 | 理由 |
|---------|------|
| `message_count` | 冗余统计 |
| `tool_call_count` | 冗余统计 |
| `source_file` | 无分析价值 |
| `duration_seconds` | 元数据 |
| `token_usage` | 元数据 |

> **保留**: `session_path` - LLM 可按需读取完整会话数据

---

## 四、改进后结构预估

```
ProtoAnalysis (改进后)
├── 必须保留: session_id, status, task_title, tool_sequence
│             failure_reason, correction
├── 建议扩充: final_output (800字符), skill_constraints (500字符)
│             completion_markers, key_intermediate_results
├── 按需查看: session_path ──────────────────────────────┐
│             (LLM可工具读取完整CanonicalSession)         │
├── 可选保留: quality_score, relevance_level, key_tools   │
└── 建议删除: message_count, tool_call_count, source_file,│
              duration_seconds, token_usage               │

预估大小: 2.5-3KB (当前1.8KB)
20个session: 约50-60KB (可接受)
```

---

## 五、总结

| 评估维度 | 当前评分 | 改进后预估 |
|---------|---------|-----------|
| 信息完整性 | ⭐⭐ | ⭐⭐⭐⭐ |
| 上下文控制 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| 对进化决策支撑 | ⭐⭐ | ⭐⭐⭐⭐ |
| 数据冗余度 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

**核心结论**：
1. **保留**: `status`、`failure_reason`、`correction`、`tool_sequence`、`task_title` 是必须的
2. **扩充**: `final_output` 严重不足，需要从300扩充到800字符
3. **新增**: `skill_constraints` 和 `completion_markers` 能显著提升进化效果
4. **删除**: 6个冗余字段可移除，减少约10-15%的数据量