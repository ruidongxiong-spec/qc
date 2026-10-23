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

默认报告标准化 EM 和 token F1；只有指定 `--judge-model` 才调用独立 judge、报告语义准确率（温度 0.1）。judge 需在同一 API 端点可用。每个样本完成或失败后立即落盘，并原子更新 summary。单条失败会保留状态、错误阶段和已完成过程，批量运行继续下一条；有运行失败时最终退出码为 1。EM/F1 以已尝试样本为分母，运行失败贡献 0，详细记录中的 metrics 为 null；completed、failed 和 outcomes 分开统计。expected 与 attempted 不同时表示尚未完成全部计划样本。比较实验应使用独立输出路径。

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

默认使用 **OpenRouter `liquid/lfm-2.5-2.6b:free` + DDGS + Jina**，无需安装或启动 Ollama。准备 Python 3.11+、Git、Clash 和 [OpenRouter API key](https://openrouter.ai/settings/keys)，然后执行：

```powershell
cd D:\qc
git -c http.proxy=http://127.0.0.1:7890 pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1
```

脚本优先读取 `OPENROUTER_API_KEY`；未设置时提示隐藏输入，仅供该 PowerShell 进程使用，不写文件或日志。运行前查询该精确模型 ID 的可用信息及免费价格，请求设置零 token 价格上限，不切换到付费模型。免费供应商不可用、模型下线或被限流时会明确报错。

脚本安装 free-web 与 tokenizers，使用 Clash HTTP 代理 `http://127.0.0.1:7890`，通过 DDGS 搜索、匿名 Jina 读取，评测清理版 HotpotQA 第一条一次。模型在 OpenRouter 远端运行；本地仅下载模型信息中对应仓库的 tokenizer.json，用于 512-token Capsule 计数，不复用 Qwen tokenizer。元数据缺少 tokenizer 仓库时需显式指定 `-TokenizerRepo` 或 `-TokenizerPath`；`-LegacyByteBudget` 可显式选择旧字节预算。默认 `json_object` 加角色结构提示和运行时校验；`-ResponseFormat json_schema` 可显式选择严格 Schema，供应商不支持时直接报错。

```powershell
# 只测试网页工具，不需要模型 key
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -CheckWebOnly
# 换数据集，仍使用同一个 OpenRouter 模型
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -InputCsv datasets/musique_ans_dev_clean1000.csv
# 显式选择以前的本地 Ollama 模式（默认不会走这里）
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -ModelProvider ollama -Model qwen2.5:7b
```

Jina 模式不做本地 DNS 预检查；通过 Clash HTTP 代理访问时，由代理解析服务域名。direct 模式保留原检查。联网成功不代表回答正确。模型用量记录包含服务返回的 cost、实际 model/provider（若有），结果仍保存至唯一的 `runs/free-one-*.jsonl`。详细配置及错误处理见 [OpenRouter 免费一题](docs/openrouter-free-one.md)。

单条 Windows 脚本保存同一前缀的文件；OpenRouter 另有 `.model.json` 记录公开模型信息：

- `runs/free-one-*.log`：配置、代码版本、阶段、调用耗时、修正原因与错误。长请求每 15 秒输出 `WAIT`。Windows 默认用 stdout 显示，避免 PowerShell 5 将 stderr 日志当作失败；日志文件为 UTF-8。
- `runs/free-one-*.jsonl`：逐题答案、参考答案、评分、状态、事件和工具审计；运行失败也有记录。
- `runs/free-one-*.summary.json`：已尝试、完成、失败、回答/部分回答/弃答统计及 EM/F1。预检查失败时只有日志，尚未开始题目评测。

常用选项：`-SkipInstall` 跳过已完成的依赖安装；`-SkipWebCheck` 显式跳过预检查（正式评测仍使用真实工具）；`-MaxSteps 8` 设置每节点步数；`-OutputRetries 1` 设置模型输出的额外修正次数；`-ModelTimeout 120` 设置单次模型 HTTP 请求超时。`-PullModel` 仅适用于显式选择的 Ollama 模式。CLI 可使用 `--verbose --log-file runs/debug.log --output-retries 1`。

Windows 默认 `SCOPE_DDGS_BACKEND=auto`，其他入口默认 `duckduckgo`，可以通过同名环境变量选择。DDGS 9.16.0 禁用了 Bing 文本引擎，`bing` 会明确报错。日志、缓存、评测来源和矩阵续跑均记录搜索配置；auto 不保证结果来自同一搜索引擎。

模型输出统一经过结构校验与有限修正；默认每次最多额外修正 1 次，执行器错误使用原有步数预算。规划持续无效或模型服务不可用会记为运行失败；节点未验证、部分结果无效或汇总无法生成有效答案会明确记为未解决/弃答。修正不会把参考答案发送给模型，也不自动删除依赖、放宽证据校验或增加步数。完整策略、故障处理矩阵和复测步骤见 [运行可靠性方案](docs/runtime-reliability.md)。
