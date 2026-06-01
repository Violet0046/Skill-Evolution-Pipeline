# Pipeline 数据流详解

本文档用**具体的数据例子**展示每个 Stage 的输入输出，帮助理解数据如何在 pipeline 中流转。

---

## 目录

1. [核心数据结构](#1-核心数据结构)
2. [Stage 1: Extract](#2-stage-1-extract)
3. [Stage 2: Filter](#3-stage-2-filter)
4. [Stage 3: Split](#4-stage-3-split)
5. [Stage 4: ProtoExtract](#5-stage-4-protoextract)
6. [Stage 5: EvidenceBuild](#6-stage-5-evidencebuild)
7. [Stage 6: Analyze](#7-stage-6-analyze)
8. [Stage 7: Evolve](#8-stage-7-evolve)
9. [完整数据流总览](#9-完整数据流总览)

---

## 1. 核心数据结构

### 1.1 CanonicalSession — 标准化会话对象

这是 pipeline 中最核心的数据结构，贯穿整个流程。一个 CanonicalSession 代表一次 AI agent 的完整对话。

```python
CanonicalSession(
    session_id="abc123-def456-789",     # 唯一标识符
    agent_id="protocol-agent",          # agent 类型
    skill_name="protocol-agent",        # 关联的技能名称
    timestamp="2026-06-01T10:00:00Z",   # 会话开始时间

    # ── 任务输入 ──
    task_input=TaskInput(
        requirement_id="REQ-042",                    # 需求ID
        requirement_title="TCP三次握手协议解析",        # 需求标题
        requirement_type="protocol",                  # 需求类型
        task_description="解析TCP三次握手协议并生成报告", # 任务描述
        raw_content="需求ID：REQ-042\n需求标题：TCP三次握手...", # 原始消息全文
        skill_content="---\nname: protocol-agent\n...",       # SKILL.md 内容
    ),

    # ── 执行追踪 ──
    execution=ExecutionTrace(
        status=ExecutionStatus.SUCCESS,    # success / failed / retry_success / unknown
        total_messages=6,                  # 总消息数
        total_tool_calls=3,                # 总工具调用数
        total_token_usage=TokenUsage(
            input_tokens=5000,             # 输入 token
            output_tokens=2000,            # 输出 token
        ),
        models_used=["mimo-v2.5-pro"],     # 使用的模型
        duration_seconds=45.0,             # 执行时长(秒)
        tool_call_details=[...],           # 工具调用详情列表
    ),

    # ── 消息列表 ──
    messages=[
        Message(role=USER,      content_text="解析TCP三次握手协议..."),
        Message(role=ASSISTANT,  content_text="我来解析这个协议。", tool_calls=[ToolCall("Read", ...)]),
        Message(role=TOOL,       tool_results=[{tool_use_id: "tu-001", content: "文件内容..."}]),
        Message(role=ASSISTANT,  content_text="协议解析完成。", tool_calls=[ToolCall("Bash", ...)]),
        Message(role=TOOL,       tool_results=[{tool_use_id: "tu-002", content: "命令执行结果"}]),
        Message(role=ASSISTANT,  content_text="最终报告已生成。完成"),
    ],

    # ── 反馈信息 ──
    feedback=Feedback(
        quality_score=8,                   # 质量评分 (0-10, 来自 sessions.jsonl)
        relevance_level="high",            # 相关性: high / medium / low
        is_direct_call=False,              # 是否直接调用
        is_retry=False,                    # 是否重试任务
        failure_reason="",                 # 失败原因 (如果有)
    ),

    # ── 元数据 ──
    metadata={
        "file_path": "/data/sessions/abc123.jsonl",  # 原始 JSONL 文件路径
        "prompt_id": "prompt-001",
    },
)
```

### 1.2 ProtoAnalysis — 轻量级摘要 (~500字节)

Stage 4 从 CanonicalSession 提取的压缩版本，用于 Stage 5 生成 evidence text。

```python
ProtoAnalysis(
    session_id="abc123-def456-789",
    status="success",
    task_title="TCP三次握手协议解析",
    task_description="解析TCP三次握手协议并生成报告",
    tool_sequence="Read→Bash→Write",          # 工具调用序列 (连续重复已去重)
    failure_reason="",                         # 失败原因 (失败时有值)
    correction="",                             # 修正建议 (失败时有值)
    final_output="最终报告已生成。",             # 最后一条 assistant 消息 (截断到300字)
    error_tool_calls=[],                       # 出错的工具调用列表
    token_usage=7000,                          # 总 token (5000+2000)
    duration_seconds=45.0,
    source_file="/data/sessions/abc123.jsonl",
    session_path="/data/sessions/abc123.jsonl",
    quality_score=8,
    relevance_level="high",
    message_count=6,
    tool_call_count=3,
    key_tools=["Read", "Bash", "Write"],       # 去重的工具名列表
)
```

### 1.3 EvolutionSuggestion — 进化建议

Stage 6 的 LLM 输出，建议如何改进 skill。

```python
EvolutionSuggestion(
    evolution_type=EvolutionType.FIX,          # fix / derived / captured
    direction="在协议解析技能中添加超时处理，当文件读取超过30秒时自动重试",
    target_skill_ids=["protocol-agent"],       # 目标技能
    category=SkillCategory.TOOL_GUIDE,         # 技能类别 (可选)
    evidence_sessions=["abc123-def456-789"],   # 支持此建议的会话ID
    evidence_session_paths=["/data/sessions/abc123.jsonl"],  # 对应文件路径
)
```

---

## 2. Stage 1: Extract

**函数**: `SessionExtractor.extract_from_file()`

**输入**: JSONL 文件路径

```jsonl
{"type": "user", "agentId": "protocol-agent", "sessionId": "abc123", "timestamp": "2026-06-01T10:00:00Z", "message": {"role": "user", "content": "需求ID：REQ-042\n需求标题：TCP三次握手\n## 任务：解析协议"}}
{"type": "assistant", "uuid": "u1", "timestamp": "2026-06-01T10:00:05Z", "message": {"role": "assistant", "model": "mimo-v2.5-pro", "content": [{"type": "text", "text": "我来解析。"}, {"type": "tool_use", "id": "tu-001", "name": "Read", "input": {"file_path": "/tmp/protocol.md"}}], "usage": {"input_tokens": 500, "output_tokens": 200}}}
{"type": "user", "uuid": "u2", "timestamp": "2026-06-01T10:00:06Z", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu-001", "content": "TCP协议文件内容..."}]}}
{"type": "assistant", "uuid": "u3", "timestamp": "2026-06-01T10:00:15Z", "message": {"role": "assistant", "model": "mimo-v2.5-pro", "content": [{"type": "text", "text": "解析完成。完成"}], "usage": {"input_tokens": 800, "output_tokens": 300}}}
```

**输出**: CanonicalSession 对象

```
CanonicalSession(
    session_id="abc123",
    agent_id="protocol-agent",
    task_input.requirement_id="REQ-042",
    task_input.requirement_title="TCP三次握手",
    execution.status=SUCCESS,
    execution.total_messages=4,
    execution.total_tool_calls=1,
    execution.total_token_usage={input: 1300, output: 500},
    messages=[USER, ASSISTANT(Read), TOOL, ASSISTANT],
    feedback.quality_score=0,  ← 此时还未从 sessions.jsonl 填充
)
```

**数据变化**: 原始 JSON 文本 → 结构化的 Python 对象

---

## 3. Stage 2: Filter

**函数**: `QualityFilter.filter_and_classify()`

**输入**: `list[CanonicalSession]` — Stage 1 输出的所有会话

```python
[
    CanonicalSession(session_id="s1", status=SUCCESS, quality_score=8, messages=[...]),
    CanonicalSession(session_id="s2", status=FAILED, quality_score=6, messages=[...]),
    CanonicalSession(session_id="s3", status=RETRY_SUCCESS, quality_score=7, messages=[...]),
    CanonicalSession(session_id="s4", status=SUCCESS, quality_score=2, messages=[...]),  ← 低分
    CanonicalSession(session_id="s5", status=UNKNOWN, messages=[]),  ← 空消息
]
```

**过滤规则** (按优先级):
1. `messages` 为空 → 丢弃 (s5)
2. `task_input.raw_content` 为空 → 丢弃
3. `quality_score < min_relevance_score` → 丢弃 (s4, score=2 < 4)
4. 按 `execution.status` 分组

**输出**: `dict[str, list[CanonicalSession]]` — 按状态分组

```python
{
    "success": [
        CanonicalSession(session_id="s1", status=SUCCESS, quality_score=8),
    ],
    "failed": [
        CanonicalSession(session_id="s2", status=FAILED, quality_score=6),
    ],
    "retry_success": [
        CanonicalSession(session_id="s3", status=RETRY_SUCCESS, quality_score=7),
    ],
}
```

**数据变化**: 5个会话 → 过滤掉2个 → 剩余3个按状态分到3组

---

## 4. Stage 3: Split

**函数**: `DatasetSplitter.split()`

**输入**: `dict[str, list[CanonicalSession]]` — Stage 2 的分组结果

```python
{
    "success": [s1, s6, s7, s8, s9, s10],    ← 6个成功会话
    "failed": [s2],                            ← 1个失败会话
    "retry_success": [s3, s11, s12],          ← 3个重试成功会话
}
```

**分割逻辑** (按每组独立分割，seed=42 保证可复现):
- 每组按 `evolution_ratio=0.7` 分割
- 只有1个会话的组 → 全部进 evolution_set (不拆分)

```python
# success 组: 6个 → 70%=4.2→4个 evolution, 2个 test
# failed 组: 1个 → 全部进 evolution
# retry_success 组: 3个 → 70%=2.1→2个 evolution, 1个 test
```

**输出**: `SamplingResult(evolution_set, test_set)`

```python
SamplingResult(
    evolution_set=[s1, s6, s7, s8, s2, s3, s11],  ← 7个会话 (用于进化)
    test_set=[s9, s10, s12],                        ← 3个会话 (用于评估)
)
```

**数据变化**: 10个会话 → 7个进化 + 3个测试

---

## 5. Stage 4: ProtoExtract

**函数**: `ProtoExtractor.extract()`

**输入**: `list[CanonicalSession]` — Stage 3 的 evolution_set

```python
[
    CanonicalSession(
        session_id="s1",
        status=SUCCESS,
        task_title="TCP三次握手协议解析",
        messages=[
            Message(USER, "解析TCP三次握手"),
            Message(ASSISTANT, "我来解析。", tool_calls=[ToolCall("Read", ...)]),
            Message(TOOL, tool_results=[{content: "文件内容"}]),
            Message(ASSISTANT, "解析完成。", tool_calls=[ToolCall("Write", ...)]),
            Message(TOOL, tool_results=[{content: "写入成功"}]),
            Message(ASSISTANT, "报告已生成。完成"),
        ],
        token_usage={input: 5000, output: 2000},
        quality_score=8,
    ),
    # ... 更多会话
]
```

**提取逻辑**:
1. `tool_sequence`: 遍历所有 assistant 消息的 tool_calls → "Read→Write" (去重连续重复)
2. `key_tools`: 去重的工具名列表 → ["Read", "Write"]
3. `final_output`: 最后一条 assistant 消息 → "报告已生成。完成"
4. `error_tool_calls`: 找 tool_result 中包含 "error" 的 → []

**输出**: `list[ProtoAnalysis]`

```python
[
    ProtoAnalysis(
        session_id="s1",
        status="success",
        task_title="TCP三次握手协议解析",
        tool_sequence="Read→Write",
        token_usage=7000,
        duration_seconds=45.0,
        message_count=6,
        tool_call_count=2,
        key_tools=["Read", "Write"],
        final_output="报告已生成。完成",
        error_tool_calls=[],
        quality_score=8,
        session_path="/data/sessions/s1.jsonl",
    ),
    # ... 更多 ProtoAnalysis
]
```

**数据变化**: CanonicalSession (~5KB) → ProtoAnalysis (~500B)，压缩约10倍

---

## 6. Stage 5: EvidenceBuild

**函数**: `EvidenceBuilder.build()`

**输入**: `list[ProtoAnalysis]` — Stage 4 的输出

```python
[
    ProtoAnalysis(session_id="s1", status="success", task_title="TCP三次握手", tool_sequence="Read→Write", token_usage=7000, ...),
    ProtoAnalysis(session_id="s2", status="failed", task_title="HTTP协议", tool_sequence="Read→Bash", token_usage=3000, failure_reason="文件不存在", ...),
    ProtoAnalysis(session_id="s3", status="success", task_title="UDP协议", tool_sequence="Read", token_usage=2000, ...),
]
```

**格式化逻辑**: 将所有 ProtoAnalysis 拼接成一个结构化文本块

**输出**: `str` — evidence text (~10KB)

```markdown
## 证据集概览
- Skill: protocol-agent
- 总会话数: 3

## 执行状态分布
- success: 2 (67%)
- failed: 1 (33%)

## 会话证据明细

### Session 1: s1
- 状态: success
- 任务: TCP三次握手
- 工具序列: Read→Write
- Token: 7000
- 消息数: 6, 工具调用数: 2
- 主要工具: Read, Write
- Session 路径: /data/sessions/s1.jsonl

### Session 2: s2
- 状态: failed
- 任务: HTTP协议
- 工具序列: Read→Bash
- Token: 3000
- 消息数: 4, 工具调用数: 2
- 主要工具: Read, Bash
- Session 路径: /data/sessions/s2.jsonl
- 失败原因: 文件不存在

### Session 3: s3
- 状态: success
- 任务: UDP协议
- 工具序列: Read
- Token: 2000
- 消息数: 3, 工具调用数: 1
- 主要工具: Read
- Session 路径: /data/sessions/s3.jsonl

## 聚合统计
- 总 Token: 12,000
- 平均 Token/会话: 4,000
- 总耗时: 120s
- 平均耗时/会话: 40.0s
- 使用的工具: Bash, Read, Write
```

**数据变化**: `list[ProtoAnalysis]` → 格式化的 markdown 文本块，作为 LLM 的输入

---

## 7. Stage 6: Analyze

**函数**: `EvidenceAnalyzer.analyze()`

**输入**:
- `evidence_text: str` — Stage 5 的输出 (上面的 markdown)
- `skill_name: str` — "protocol-agent"
- `session_count: int` — 3

**LLM 处理**:
1. 加载 prompt 模板 (`evidence_analysis_system.txt`, `evidence_analysis_user.txt`)
2. 将 evidence_text 填入 user prompt
3. LLM 可以调用工具查看具体会话详情 (read_session_summary, read_session_messages, read_session_tool_detail)
4. LLM 返回 JSON 格式的分析结果

**输出**: `ExecutionAnalysis` 对象

```python
ExecutionAnalysis(
    raw={
        "skill_name": "protocol-agent",
        "total_sessions": 3,
        "success_count": 2,
        "failed_count": 1,
        "success_rate": 0.67,
        "dominant_patterns": [
            {"pattern": "Read→Write 流程", "frequency": 2, "description": "成功的会话都遵循读取→写入的模式"}
        ],
        "failure_analysis": {
            "root_causes": [
                {"cause": "文件路径不存在", "frequency": 1, "impact": "high"}
            ]
        },
        "skill_gaps": [
            {"gap": "缺少文件存在性检查", "impact": "high", "sessions": ["s2"]}
        ],
        "evolution_suggestions": [
            {
                "type": "fix",
                "direction": "在协议解析技能中添加文件存在性检查，读取文件前先验证路径是否存在",
                "target_skills": ["protocol-agent"],
                "evidence_sessions": ["s2"],
                "evidence_session_paths": ["/data/sessions/s2.jsonl"],
            },
            {
                "type": "derived",
                "direction": "基于成功的TCP/UDP解析经验，创建通用协议解析模板",
                "target_skills": ["protocol-agent"],
                "evidence_sessions": ["s1", "s3"],
                "evidence_session_paths": ["/data/sessions/s1.jsonl", "/data/sessions/s3.jsonl"],
            },
        ],
    },
    # 解析后的属性:
    skill_name="protocol-agent",
    total_sessions=3,
    success_count=2,
    failed_count=1,
    success_rate=0.67,
    evolution_suggestions=[
        EvolutionSuggestion(type=FIX, direction="添加文件存在性检查..."),
        EvolutionSuggestion(type=DERIVED, direction="创建通用协议解析模板..."),
    ],
)
```

**数据变化**: evidence text (文本) → 结构化的分析结果 + 2条进化建议

---

## 8. Stage 7: Evolve

**函数**: `SkillEvolver.evolve()`

**输入**:
- `analysis: ExecutionAnalysis` — Stage 6 的输出
- `skill_content: str` — 当前 SKILL.md 的内容
- `skill_dir: Path` — SKILL.md 所在目录

**处理流程** (对每条 evolution_suggestion):

```
evolution_suggestions[0]: FIX — "添加文件存在性检查"
    ↓
1. _build_fix_prompt() — 构建包含当前 SKILL.md 内容的 prompt
2. _call_llm_with_tools() — LLM 可以查看证据会话详情
3. LLM 输出 change 格式:
    # Change 001
    summary: 添加文件读取前的存在性检查

    anchor:
      type: heading
      selector: "## 文件读取规范"

    operation: INSERT_SUBSECTION

    new_content: |
      ### 存在性检查
      在调用 Read 工具前，先用 Bash 运行 `test -f <path>` 验证文件存在。

4. _apply_fix() — 生成 .change 文件
```

**输出**: `EvolutionRunResult`

```python
EvolutionRunResult(
    analysis=ExecutionAnalysis(...),
    results=[
        EvolutionResult(
            suggestion=EvolutionSuggestion(type=FIX, direction="添加文件存在性检查"),
            edit_result=SkillEditResult(skill_dir=Path("output/staging/protocol-agent/changes/run_xxx/")),
            change_summary="添加文件读取前的存在性检查",
            ok=True,
        ),
        EvolutionResult(
            suggestion=EvolutionSuggestion(type=DERIVED, direction="创建通用协议解析模板"),
            edit_result=SkillEditResult(skill_dir=Path("output/staging/protocol-agent/changes/run_xxx/")),
            change_summary="基于TCP/UDP经验创建通用模板",
            ok=True,
        ),
    ],
)

# 属性:
run_result.success_count  # 2
run_result.fail_count     # 0
```

**生成的文件**:

```
output/staging/protocol-agent/changes/run_20260601_100000/
├── 001.change          ← 第1条建议的变更描述 (YAML格式)
├── 001.raw             ← LLM 原始输出 (调试用)
├── 002.change          ← 第2条建议的变更描述
├── 002.raw             ← LLM 原始输出
└── versions.json       ← 所有变更的清单
```

**001.change 文件内容**:

```yaml
# Change 001
suggestion_id: fix-protocol-agent
suggestion_type: fix
priority: high

summary: 添加文件读取前的存在性检查

anchor:
  type: heading
  selector: "## 文件读取规范"

operation: INSERT_SUBSECTION

new_content: |
  ### 存在性检查
  在调用 Read 工具前，先用 Bash 运行 `test -f <path>` 验证文件存在。
  如果文件不存在，直接返回错误信息，不要尝试读取。
```

**数据变化**: 进化建议 → LLM 生成具体变更 → .change 文件 (可被后续 merge 流程应用)

---

## 9. 完整数据流总览

```
sessions.jsonl (原始JSONL文件, 每行一个JSON对象)
    │
    ▼ Stage 1: SessionExtractor.extract_from_file()
    │
list[CanonicalSession] (结构化的会话对象, ~5KB/个)
    │  例: [session_001, session_002, session_003, session_004, session_005]
    │
    ▼ Stage 2: QualityFilter.filter_and_classify()
    │
dict[str, list[CanonicalSession]] (按状态分组)
    │  {
    │    "success":       [session_001, session_003],
    │    "failed":        [session_002],
    │    "retry_success": [session_005],
    │  }
    │  (session_004 被过滤: quality_score 太低)
    │
    ▼ Stage 3: DatasetSplitter.split()
    │
(evolution_set, test_set) (70/30 分割)
    │  evolution_set: [session_001, session_002, session_005]  ← 用于进化
    │  test_set:      [session_003]                            ← 用于评估
    │
    ▼ Stage 4: ProtoExtractor.extract()
    │
list[ProtoAnalysis] (轻量级摘要, ~500B/个)
    │  [proto_001, proto_002, proto_005]
    │
    ▼ Stage 5: EvidenceBuilder.build()
    │
evidence_text (格式化的 markdown 文本, ~10KB)
    │  "## 证据集概览\n- Skill: protocol-agent\n- 总会话数: 3\n..."
    │
    ▼ Stage 6: EvidenceAnalyzer.analyze() ← 1次 LLM 调用
    │
ExecutionAnalysis (分析结果 + 进化建议)
    │  {
    │    "success_rate": 0.67,
    │    "evolution_suggestions": [
    │      {type: "fix", direction: "添加文件存在性检查"},
    │      {type: "derived", direction: "创建通用协议解析模板"},
    │    ]
    │  }
    │
    ▼ Stage 7: SkillEvolver.evolve() ← N次 LLM 调用 (每条建议一次)
    │
N个 .change 文件 (原子变更)
    │  001.change: "添加文件存在性检查"
    │  002.change: "创建通用协议解析模板"
    │
    ▼ (后续流程: merge LLM 将 .change 应用到 SKILL.md)
    │
最终的 SKILL.md (更新后的技能文件)
```

---

## 附录: 数据结构关系图

```
CanonicalSession
├── session_id ──────────────────────┐
├── task_input: TaskInput            │
│   ├── requirement_id               │
│   ├── requirement_title            │
│   └── task_description             │
├── execution: ExecutionTrace        │
│   ├── status: ExecutionStatus      │
│   ├── total_messages               │
│   ├── total_tool_calls             │
│   └── total_token_usage            │
├── messages: list[Message]          │
│   ├── role: MessageRole            │
│   ├── content_text                 │
│   ├── tool_calls: list[ToolCall]   │
│   └── tool_results: list[dict]     │
├── feedback: Feedback               │
│   └── quality_score ───────────────┼──→ Stage 2 过滤依据
└── metadata: dict                   │
    └── file_path ───────────────────┼──→ Stage 7 tool-use 查看详情
                                     │
ProtoAnalysis ←──────────────────────┘ (从 CanonicalSession 提取)
├── session_id
├── status
├── tool_sequence        ← "Read→Bash→Write"
├── token_usage          ← 7000
├── key_tools            ← ["Read", "Bash", "Write"]
├── failure_reason       ← "文件不存在" (失败时)
└── session_path         ← "/data/sessions/s1.jsonl"

EvolutionSuggestion ←── Stage 6 LLM 输出
├── evolution_type       ← FIX / DERIVED / CAPTURED
├── direction            ← "添加文件存在性检查"
├── target_skill_ids     ← ["protocol-agent"]
└── evidence_sessions    ← ["s1", "s2"]
```
