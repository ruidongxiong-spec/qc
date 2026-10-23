# SCOPE — Python 独立复现

依据用户提供的论文 **From Trajectories to States: State-Constrained Plan Execution for LLM Agents** 实现。当前是方法级独立复现，不是作者原始代码，也未复现论文表格中的准确率。

## 快速开始

Python 3.11+，运行时只使用标准库，无 GPU 或数据库要求。

```bash
cd /workspace/qc
python3 -m venv .venv
.venv/bin/python -m pip install -e .
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
| `SERPER_API_KEY` | Serper 搜索，必需 |
| `JINA_API_KEY` | Jina Reader 可选认证；无密钥时受公共服务限流 |

模型服务必须支持 JSON object response format。远程联网需要放行模型端点域名、`google.serper.dev`、`r.jina.ai`。访问目标网页经 Jina Reader 代理；具体页面可用性需另测。默认 HTTP 客户端保留 TLS 验证；不自动重试付费请求。

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
