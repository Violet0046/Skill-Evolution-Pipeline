#!/usr/bin/env python3
"""调用 EvidenceAnalyzer.analyze() 生成 ExecutionAnalysis_output.json"""

import sys
sys.path.insert(0, "/home/10358563/Code/Skill Evolution Pipeline/Self-Evolution-Pipeline")

import os
import json
from pathlib import Path

# 设置 API 环境变量
os.environ["OPENAI_API_KEY"] = "sk-BZJ4EbDAttoZ4yX-PoLVaw"
os.environ["OPENAI_API_BASE"] = "http://10.239.226.8:9000/v1"

from skill_evolution.config.settings import LLMConfig
from skill_evolution.config.prompts import PromptLoader
from skill_evolution.llm.evidence_analyzer import EvidenceAnalyzer

# 读取 evidence_text.md
evidence_path = Path("/home/10358563/Code/Skill Evolution Pipeline/Self-Evolution-Pipeline/output/runs/run_20260602_134151/evidence_text.md")
evidence_text = evidence_path.read_text(encoding="utf-8")

# 参数
skill_name = "查询需求信息"
session_count = 198

print(f"读取 evidence_text.md: {len(evidence_text)} 字符")
print(f"skill_name: {skill_name}")
print(f"session_count: {session_count}")

# 创建 LLM 配置
config = LLMConfig(
    model="MiniMax-M2.7",
    api_key="sk-BZJ4EbDAttoZ4yX-PoLVaw",
    api_base="http://10.239.226.8:9000/v1",
    max_tokens=4096,
)

# 加载 prompt
prompts_dir = Path("/home/10358563/Code/Skill Evolution Pipeline/Self-Evolution-Pipeline/prompts")
prompt_loader = PromptLoader(prompts_dir)

# 创建 EvidenceAnalyzer (不使用 sessions)
analyzer = EvidenceAnalyzer(config=config, prompt_loader=prompt_loader, sessions=None)

# 调用 analyze
print("\n调用 EvidenceAnalyzer.analyze()...")
print("这可能需要几分钟...")
try:
    analysis = analyzer.analyze(evidence_text, skill_name, session_count)
    print(f"分析完成!")

    # 输出到文件
    output_path = Path("/home/10358563/Code/Skill Evolution Pipeline/ExecutionAnalysis_output.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(analysis.to_dict(), f, ensure_ascii=False, indent=2)

    print(f"\n结果已保存到: {output_path}")
    print(f"文件大小: {output_path.stat().st_size} 字节")

    # 打印摘要
    print("\n" + "="*60)
    print(analysis.summary())
    print("="*60)

except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()