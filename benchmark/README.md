# Rulerything Benchmark

规则系统综合对比测试 — 量化对比使用/不使用 Rulerything 规则系统时的代码质量差异。

## 快速开始

```bash
# 确保规则服务运行中
python main.py

# 运行全部 benchmark
python benchmark/runner.py

# 只跑单个场景
python benchmark/runner.py --scene "SQL 注入防护"

# 输出 JSON 格式
python benchmark/runner.py --json

# 查看报告
cat benchmark/report/comparison_report.md
```

## 测试场景

| 场景 | 分类 | 难度 | 说明 |
|------|------|------|------|
| SQL 注入防护 | security | easy | 数据库查询的安全实现 |
| 数据库索引优化 | performance | medium | 高并发下的大数据量分页查询 |
| Python 异步编程 | performance | medium | 并发爬虫的稳健实现 |
| 错误处理模式 | error-handling | medium | CSV 文件处理器 |
| REST API 设计 | api-design | hard | 完整的用户管理 API |

## 评分模型

```
score = 40 (基础分)
     + bugs_prevented × 8    # 每个规则能预防的 bug
     + best_practices × 5    # 每条遵循的最佳实践
     + edge_cases × 4        # 每个处理的边界情况
     - token_overhead / 100  # 规则 Token 开销扣分
     = total (0-100)
```

## 输出

- `report/comparison_report.md` — 详细 Markdown 对比报告
- `report/benchmark_results.json` — (`--json` 模式) 结构化结果数据
