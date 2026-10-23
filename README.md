# SCOPE — Python 独立复现

依据用户提供的论文 **From Trajectories to States: State-Constrained Plan Execution for LLM Agents** 实现。当前是方法级独立复现，不是作者原始代码，也未复现论文表格中的准确率。

## 快速开始

Python 3.11+，核心引擎只使用标准库，无 GPU 或数据库要求；免费网页搜索/正文提取使用可选依赖。

```bash
cd /workspace/qc
python3 -m venv .venv
.venv/bin/python -m pip install -c requirements-free-web.lock -e ".[free-web]"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m scope.cli demo
```

离线 demo 使用固定的工具/模型替身，执行两个依赖节点：查询 Ada Lovelace 出生年份，然后计算 1843−1815，预期输出 `28`。它验证工程链路，不代表真实检索或 LLM 推理能力。结果、Capsule、DAG 和事件保存在 `runs/result.json`。

## 真实模型与联网工具

在远程机器安全配置环境变量；不要提交 `.env` 或密钥。

| 变量 | 用途 |
|---|---|
| `SCOPE_BASE_URL` | 兼容 OpenAI 的 API 根路径，含 `/v1`，如本地推理服务 `http://127.0.0.1:8000/v1` |
| `SCOPE_MODEL` | 服务实际提供的完整模型 ID；论文的模型名称不足以确定具体 checkpoint |
| `SCOPE_API_KEY` | 端点要求认证时使用 |
| `SCOPE_SEARCH_PROVIDER` | 默认 `ddgs`；可选 `searxng` / `wikipedia` / `serper` |
| `SCOPE_READER_PROVIDER` | 默认 `direct`；可选 `jina` |
| `SEARXNG_BASE_URL` | 选择 SearXNG 时填写开启 JSON API 的实例 URL |
| `SERPER_API_KEY` | 仅选择 Serper 时必需 |
| `JINA_API_KEY` | Jina Reader 可选认证；无密钥时受公共服务限流 |

模型服务必须支持 JSON object response format。远程联网需要放行实际模型端点、搜索提供者以及访问的网页域名。选择 Jina 时网页经 `r.jina.ai` 代理；选择 direct 则访问目标站点。具体可用性需实测。默认 HTTP 客户端保留 TLS 验证；不自动重试付费请求。

```bash
.venv/bin/python -m scope.cli run --query '你的多跳问题' --output runs/live.json
```

Python 工具目前仅支持受限算术表达式，不执行任意 Python。这是显式实现差异，不应当视为论文通用沙箱的完整替代。不要将模型代码直接交给宿主机执行。

## 评测

自行准备并记录合法来源、版本、划分、抽样种子和样本 ID。输入 JSONL 每行：

```json
{"id":"sample-1","question":"...","answer":["参考答案","同义答案"]}
```

`examples/questions.jsonl` 仅是格式示例，不是论文数据集。

```bash
.venv/bin/python -m scope.cli evaluate --input /path/to/data.jsonl --output runs/eval.jsonl --runs 3
.venv/bin/python -m scope.cli evaluate --input /path/to/data.jsonl --output runs/judged.jsonl --runs 3 --judge-model gpt-4o
```

默认报告标准化 EM 和 token F1；只有指定 `--judge-model` 才调用独立 judge、报告语义准确率（温度 0.1）。judge 需在同一 API 端点可用。每个样本立即落盘，异常会以非零退出码中止，不把失败计成成功；只在完整执行后写 summary。不要将中断运行遗留的 summary 当作本次结果。比较实验应使用独立输出路径。

支持 `--ablation no-dag|no-memory|no-verification`。这是可操作的研究变体，论文未提供原始消融提示词，不能断言等价于作者设置。`no-memory` 在状态之外加入本地与父节点原始轨迹，保留输出 Capsule 接口；详细差异见 [复现说明](docs/reproduction.md)。

## 项目结构

- `scope/core.py`：DAG 校验、执行循环、验证门控、局部重试、超时隔离和审计事件。
- `scope/backends.py`：角色提示词、兼容 API 客户端、离线模型替身。
- `scope/tools.py`：Serper、Jina 和受限算术。
- `scope/evaluation.py`、`scope/cli.py`：评分与命令行。
- `tests/`：离线回归测试，含 HTTP 接口契约测试。

当前不包含前后端系统或未经验证的第二创新点。这两部分应在真实模型复现和失败样本分析后推进。

## Windows、模型比较与展示

见 [多模型性能评测](docs/model-performance.md) 和 [架构/展示/免费替代调研](docs/system-demo.md)。Windows 安装入口为 `scripts/setup-windows.ps1`，默认目录 `D:\qc`。

```bash
python -m scope.benchmark --models examples/models.json --input examples/questions.jsonl --output runs/matrix-demo --runs 3 --demo
python -m scope.report --input runs/matrix-demo/summary.json --output runs/matrix-demo/report.html
```

`--demo` 只检查评测流水线。真实运行前必须替换 models.json 的占位模型 ID、配置凭据并去掉 `--demo`；不能据此生成模型排名。

## 已包含的论文相关数据

真实 CSV 已放在 [`datasets/`](datasets/README.md)：HotpotQA 开发集 7,405 题、MuSiQue 开发集 QA 镜像导出 2,417 题、Bamboogle 125 题，以及固定的 1,000/1,000/125 评测抽样和合并版 2,125 题。评测 CLI 和模型矩阵现在直接接受 CSV，无需转成 JSONL。

这些是带 GitHub 来源、固定 commit、校验和、许可说明的公开数据导出，**不是论文作者原始抽样清单**。HotpotQA 这份 dev 全部为 hard；MuSiQue 为镜像规范化 QA 视图，不能冒充官方完整 raw 格式。数据来源、字段与精确限制见 datasets/README.md。

## 最新数据与项目进度

官方 HotpotQA fullwiki 三个划分现已提供 CSV：含上下文的全部原字段位于 `datasets/official/hotpotqa_fullwiki/` 的 34 个分片，每片不超过 20 MiB；轻量 QA 位于 `datasets/hotpotqa_fullwiki_*.csv`。新固定抽样为 `datasets/hotpotqa_train_sample1000.csv`（easy 500 / medium 300 / hard 200），新批测输入为 `datasets/scope_selected2125.csv`。旧 dev hard 基线、MuSiQue 2/3-hop 抽样和 Bamboogle 均保留。用户已暂停清理，不将审阅草稿视为已完成清理。

见 [背景、实现进度及后续任务](docs/project-status.md)、[数据口径与重建](docs/hotpotqa-sampling.md)。已有 Windows Git 副本可用 `scripts/setup-windows.ps1 -Update -FreeWeb` 安全同步 main；云环境不能直接写用户电脑的 D 盘。
