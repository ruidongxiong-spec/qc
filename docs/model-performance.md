# 文件一：多模型性能评测与复现协议

## 当前结论

已具备批量对比工具，但**尚无真实模型性能排名**。当前没有模型 API 凭据，示例 model ID 是占位符。`--demo` 只检验评测器，不表示任何真实模型的准确率或速度。

## 对比矩阵

第一阶段用同一可用端点的 2–3 个实际模型 ID 做 10–20 题通路验证；第二阶段固定版本/抽样后扩大规模。Qwen3 checkpoint 必须根据服务商实际目录选择，不能用“80B”等论文简写代替真实 ID。可加入一个其他系列模型检验泛化性，但先保证上下文长度、JSON 支持、采样温度和工具配置一致。

将 `examples/models.json` 复制为本地配置并替换模型 ID。每行配置包含 name、base_url、model、api_key_env；只写密钥变量名称。OpenRouter 的 `:free` 型号和可用额度随时变化，且通常仍需账户 API key；当前不指定未经验证的“永久免费”型号。免费模型适合联调，严肃比较应记录服务商返回模型版本和限流失败。

```powershell
# Windows，先在当前进程或安全的系统设置中配置 OPENROUTER_API_KEY。
cd D:\qc
.\.venv\Scripts\python.exe -m scope.benchmark --models examples/models.json --input examples/questions.jsonl --output runs/model-comparison --runs 3 --search ddgs --reader direct
```

Linux 使用 `.venv/bin/python`，其他参数相同。这里的单题数据只是命令示例，请替换成研究数据集。

中断后带 `--resume` 继续同一目录：已写下的成功和错误记录均不重复执行；如果要重测错误，使用新目录。配置/数据指纹不一致时拒绝续跑。JSONL 尾行损坏时明确失败，先保留原件再人工检查，不能悄悄丢数据。

## 已实现的结果文件

| 文件 | 内容 |
|---|---|
| manifest.json | 数据和配置指纹、模型标签、工具配置、是否 demo |
| records.jsonl | 每模型/每次运行/每题的状态、答案、Capsule、事件、usage、耗时、检索审计 |
| summary.json | 成功/错误数、EM/F1、平均与 P95 延迟、验证节点率、token 统计 |
| retrieval-cache/ | 请求标识、来源、抓取时间、观察内容；缓存命中不会重新请求 |

`em_success_only` 和 `f1_success_only` 只统计成功请求；`em_all_attempts` 将错误请求当未答对，防止限流导致的选择偏差。`mean_seconds`/`p95_seconds` 当前仅统计成功请求，总体失败耗时保留在 records。token 缺失显示 null 并给出 coverage，不伪造零成本。`verified_node_rate` 是实现状态指标，不是事实正确率，且无验证消融中不具有“已独立验证”的含义。消费费用尚未估算，需按供应商账单或实际单价另算。

矩阵评测目前报告 EM/F1；论文采用 GPT-4o 语义判断。语义 judge 可先用 `scope.cli evaluate --judge-model ...` 单独评测；矩阵模块尚未接入统一 judge。必须区分这两套指标，不用 EM 冒充论文 accuracy。

```bash
python -m scope.report --input runs/model-comparison/summary.json --output runs/model-comparison/report.html
```

## 公平性控制

1. 冻结 HotpotQA/MuSiQue/Bamboogle 来源、版本、划分、题目 ID、抽样种子及答案格式，不混入演示题。
2. 同一节点步骤预算、提示词、工具提供者、区域、语言、读取器和模型采样参数；模型默认上下文/推理开关也应记录在实验登记中。
3. 缓存仅固定**相同工具请求**。不同模型会生成不同查询，因此共享缓存不等于所有模型获得同样证据；若研究纯推理能力，另建固定检索语料实验，不能将其与 live-web 结果混用。
4. 三次运行先分别报告每次准确率，再报告均值和波动。目前 summary 是三次合并均值，逐次统计可由 records 分组得到，尚未自动生成置信区间。
5. 区分格式错误、API 故障、工具失败、节点超时和答案错误。错误记录目前仅保存异常类型以避免泄露凭据，详细运行排查须在受控环境复查。
6. 缓存、程序版本、依赖锁定文件一并归档；不要跨版本复用检索缓存。当前 cache identity 有版本号但没有自动源码哈希，升级检索逻辑需新建目录/提升版本。
7. 完整版与消融版本单独运行；原始 no-memory 仍为近似轨迹增强版本，不能视为作者原始消融。

## 下一轮应补的研究能力

按优先级：真实 tokenizer 的 512-token Capsule 预算；完整基准导入与分组；统一独立 judge；逐次统计及 bootstrap 区间；固定证据基线；工具/模型错误分类与账单成本；对齐模型的 ReAct/MindSearch。当前仍以 UTF-8 字节预算保守近似 tokenizer，这会影响性能，正式表格前必须解决。

## 当前仓库内可用数据

公开完整数据和抽样 CSV 已落地，详见 [`../datasets/README.md`](../datasets/README.md)。当前统一输入为 `--input datasets/scope_selected2125.csv`：HotpotQA fullwiki train 按 easy 500 / medium 300 / hard 200，MuSiQue-Ans dev 按 2-hop 600 / 3-hop 400，Bamboogle 125。也可分别输入 `hotpotqa_train_sample1000.csv`、`musique_ans_dev_sample1000.csv` 和 `bamboogle.csv`。两份 1,000 题都是本项目固定抽样，未声称匹配作者原始 ID，用户已暂停语义清理。`scope_public_baseline2125.csv` 与旧 MuSiQue 500/500 仅保留追溯。

最新批测输入使用 `datasets/scope_selected2125.csv`：HotpotQA 已调整为官方 train 原标签 easy 500 / medium 300 / hard 200。旧合并版仍是 dev hard 基线。用户已暂停清理，不能称为清理通过；来源和限制见 [数据口径](hotpotqa-sampling.md)。未跑真实模型前不报告排名。
