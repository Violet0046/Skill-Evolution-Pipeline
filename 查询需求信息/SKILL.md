---
name: 查询需求信息
description: 查询RDC工作项/需求信息。当用户想要查询工作项的标题、描述、状态、字段值等信息时使用此技能。支持按标识查询、筛选指定字段、组合条件查询。
---

## 认证配置

调用此技能优先从项目根目录 `.env` 读取 `userid` 和 `token`：
- 推荐在项目根 `.env` 中设置：`userid=你的工号`、`token=你的token`
- 也可通过命令行参数 `-u/-t` 显式传入覆盖 `.env`

命令行示例：
```bash
python ${CLAUDE_SKILL_DIR}/scripts/query_simple.py -c "标识=RAN-1995001" -s "标题,描述"
```

# 查询需求信息技能

## 概述

此技能用于查询研发云（RDCloud）中的工作项/需求信息。支持查询指定字段、组合条件筛选。

## 使用场景

- 查询工作项的基本信息（标题、状态、类型等）
- 查询特定字段值（如描述、算法文档链接等）
- 按条件筛选工作项

## 调用脚本

调用当前技能目录下的脚本：
```bash
python ${CLAUDE_SKILL_DIR}/scripts/query_simple.py [参数]
```

## 参数说明

| 参数 | 简写 | 说明 |
|-----|------|------|
| --select-items | -s | 查询的字段，英文逗号分隔 |
| --conditions | -c | 查询条件 |
| --order-by | -o | 排序字段 |
| --limit | -n | 返回条数，默认20 |
| --workspace | -w | 工作区 |
| --userid | -u | 用户ID |
| --token | -t | 认证Token |

> **默认查询字段**：当用户未指定查询字段时，默认查询以下四个字段：
> - 标题
> - 描述
> - 算法文档链接
> - 需求分析文档链接

## 常用字段

### 条件字段（用于 --conditions）
- 标识：工作项ID，如 RAN-1995001
- 标题：工作项标题
- 状态：工作项状态
- 工作项类型：需求类型

### 查询字段（用于 --select-items）
- 标题
- 描述
- 算法文档链接
- 需求分析文档链接
- 其他API字段名

## 示例

### 查询单个工作项的指定字段
```bash
python ${CLAUDE_SKILL_DIR}/scripts/query_simple.py -c "标识=RAN-1995001" -s "标题,描述,算法文档链接,需求分析文档链接"
```

### 按状态查询
```bash
python ${CLAUDE_SKILL_DIR}/scripts/query_simple.py -c "状态=进行中" -s "标识,标题,状态"
```

### 多条件查询
```bash
python ${CLAUDE_SKILL_DIR}/scripts/query_simple.py -c "工作项类型=MR AND 状态=已关闭" -s "标识,标题,状态,更新时间"
```

### 排序和限制
```bash
python ${CLAUDE_SKILL_DIR}/scripts/query_simple.py -c "状态=进行中" -s "标识,标题" -o "更新时间 desc" -n 10
```

## 注意事项

1. 工作区会自动从标识中提取（如 RAN-1995001 → RAN）
2. 字段名支持中文和API字段名混用
3. 不在映射表中的字段会直接透传为API字段名
4. 初始化流程应优先通过本技能查询需求信息，不再使用 `rdc-mcp` 工具。

## 输出要求

**直接返回脚本输出，不要总结或改写查询结果。**
