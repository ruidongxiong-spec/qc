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

真实 CSV 已放在 [`datasets/`](datasets/README.md)：HotpotQA fullwiki 全量 train/validation/test、MuSiQue 官方 Ans/Full 全量 train/dev/test、Bamboogle 125 题，以及固定的 1,000/1,000/125 评测抽样和合并版 2,125 题。评测 CLI 和模型矩阵现在直接接受 CSV，无需转成 JSONL。

这些是带 GitHub 来源、固定 commit、校验和、许可说明的公开数据导出，**不是论文作者原始抽样清单**。HotpotQA 这份 dev 全部为 hard；旧 MuSiQue 文件为镜像 QA 视图；新版已直接取得作者 v1.0 ZIP，完整原字段 CSV 见 `datasets/official/musique_ans/` 与 `musique_full/`。数据来源、字段与精确限制见 datasets/README.md。

## 最新数据与项目进度

官方 HotpotQA fullwiki 三个划分已提供完整原字段分片和轻量 QA CSV。当前批测输入为 `datasets/scope_selected2125.csv`，合并逐条题面复核后的 `hotpotqa_train_clean1000.csv`（easy 500 / medium 300 / hard 200）、`musique_ans_dev_clean1000.csv`（Ans dev，2-hop 600 / 3-hop 400）和 Bamboogle 125 题。两份清理版相对初抽累计分别替换 133、221 条；完整来源和原始 sample1000 保留，不新增逐题记录。所有 2,000 题的题面均已逐条阅读，疑点结合官方支持材料检查，这不代表全量答案的独立事实核验。

见 [背景、实现进度及后续任务](docs/project-status.md)、[数据口径与重建](docs/hotpotqa-sampling.md)。已有 Windows Git 副本可用 `scripts/setup-windows.ps1 -Update -FreeWeb` 安全同步 main；云环境不能直接写用户电脑的 D 盘。

MuSiQue 官方 Ans/Full 的 train、dev、test 六份完整数据已导出 CSV（45 个 ≤20 MiB 分片），另有 Ans 全量轻量 QA 和 1,000 题抽样。来源、重建及字段见 [MuSiQue 数据说明](docs/musique-datasets.md)。

后续数据审阅、模型与检索 API 修改、论文对齐、性能测试、展示和本地部署的优先级及验收标准见 [TODO 清单](docs/TODO.md)。

## Windows 免费运行一条真实数据

先安装 Python 3.11+、Git 和 [Ollama](https://ollama.com/download/windows)。在 `D:\qc` 的 PowerShell 中同步并运行：

```powershell
git pull --ff-only origin main
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -PullModel
```

脚本安装锁定的 free-web 依赖，使用 Clash HTTP 代理 `http://127.0.0.1:7890`，以 DDGS 搜索、匿名 Jina 读取，再通过本地 Ollama 的 `qwen2.5:7b` 运行清理版 HotpotQA 第一条数据一次。模型下载需要数 GB 空间，推理速度及可用模型大小取决于本机内存/显存；不调用付费模型 API。结果保存到唯一的 `runs/free-one-*.jsonl`，完成不代表答案正确，请查看答案、证据和 EM/F1。脚本仅修改当前进程环境变量，不保存密钥。

```powershell
# 仅测试网页工具，不需要 Ollama
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -CheckWebOnly
# 已下载模型时直接运行；按实际 Clash HTTP 端口调整
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -ProxyUrl http://127.0.0.1:7890
# 更换本地模型或数据集
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -Model qwen2.5:7b -InputCsv datasets/musique_ans_dev_clean1000.csv
# 已装好依赖后，独立检查搜索、读取及搜索到读取链路
.\.venv\Scripts\python.exe -m scope.web_smoke
```

Jina 模式将网页地址交给 Jina 处理，不做本地 DNS 预检查或地址拦截；通过 Clash HTTP 代理连接时，由代理解析服务域名。direct 模式保留原有本地检查。DDGS/Jina 公共访问可能限流；连接成功不保证所有网页可读取。Ollama 必须运行，`-PullModel` 仅负责下载模型。网页检查成功后才会测试本地模型的 JSON 响应并执行真实评测。
