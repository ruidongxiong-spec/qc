# OpenRouter LFM 免费运行一题

默认模型使用用户从实时 OpenRouter 目录确认的 `liquid/lfm-2.5-2.6b:free`，地址 `https://openrouter.ai/api/v1/chat/completions`。`:free` 是该模型 ID 的组成部分，必须保留；不替换为同系列的付费模型。DDGS/Jina、数据集、每节点 8 步、状态分段提取、独立验证和结果评分保持原流程。

## 启动

保持 Clash 7890 端口可用，在 PowerShell 执行：

```powershell
cd D:\qc
git -c http.proxy=http://127.0.0.1:7890 pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1
```

脚本默认 `-ModelProvider openrouter`，默认模型如上。不使用本地 7B，不需要 Ollama 或 `-PullModel`。如果当前进程没有 `OPENROUTER_API_KEY`，脚本会提示隐藏输入。key 仅进入进程环境和 Authorization 头，不写入日志、结果或仓库。创建 key：https://openrouter.ai/settings/keys 。已设置环境变量则无需再次输入。

免费模型实际可用性由 OpenRouter 当时的模型列表、供应商和配额决定。脚本先查询 `/api/v1/models` 核对精确 ID 和零输入/输出价格；元数据包含固定请求费用时也须为零。生成请求携带 `provider.max_price.prompt=0`、`completion=0` 和 `require_parameters=true`。没有符合条件的路由就报错，不自动放宽价格或换模型。

## 模型和 tokenizer 配置

- OpenRouter 使用标准 Chat Completions、Bearer key、非流式响应、`max_tokens` 和 `response_format`；不传 Ollama 的 `num_ctx/num_predict`，也不传用户提供的 LFM 能力列表未列出的 `top_p`。
- 默认请求 `reasoning={"enabled":false}`，避免可选推理占用供 JSON 正文使用的生成额度；这与只隐藏推理文本的 `exclude=true` 不同。`-Reasoning auto` 可显式省略控制、使用服务默认值。供应商不支持该参数时明确报错，不自动取消限制。
- 默认 `json_object`，将角色所需 JSON 结构放入提示并继续运行时校验。模型声明支持 `response_format` 并不单独证明每个供应商支持完整严格 JSON Schema，因此不默认开启严格模式。需要时可显式 `-ResponseFormat json_schema`；不支持就失败，不静默降级。
- 通过模型列表的 `hugging_face_id` 获取对应 tokenizer 仓库，再按固定 revision 下载 tokenizer.json 并记录 SHA-256。本地不下载模型权重，也不使用已有 Qwen tokenizer 计数 LFM 输出。
- 若模型信息没有 tokenizer 仓库，可显式 `-TokenizerRepo owner/repo` 或 `-TokenizerPath D:\models\tokenizer.json`。找不到文件会停止；`-LegacyByteBudget` 仅在用户显式选择时启用旧的 512 UTF-8 字节预算，不冒充论文的 512 tokens。
- `-ContextTokens` 只控制可选 Ollama 模式。OpenRouter 的模型上下文长度见同前缀 `.model.json`；服务器自行执行上下文限制。相同 Hugging Face tokenizer 与远端量化/模板设置是否完全一致仍需供应商确认。

通用 Python CLI 也可设置 `SCOPE_BASE_URL=https://openrouter.ai/api/v1`、`SCOPE_MODEL=liquid/lfm-2.5-2.6b:free`、`SCOPE_MODEL_API=openai`、`SCOPE_RESPONSE_FORMAT=json_object`、`OPENROUTER_API_KEY` 和匹配的 `SCOPE_TOKENIZER_PATH`。CLI 不执行脚本的模型列表预检查，但该 OpenRouter 地址的生成请求始终限制零 token 价格路由。

## 结果和故障

同一运行前缀下：`.log` 是进度诊断，`.model.json` 是公开模型/价格/tokenizer 信息，`.jsonl` 是正式一题结果，`.summary.json` 是评分汇总。仅预检查成功没有答题成绩。每次模型调用保留服务返回的 token 用量、cost、实际模型和供应商（若有）。输出契约版本记录为 v2，防止矩阵续跑混用不同契约。

- 401：检查 key 是否有效；不打印完整 key。
- 402/403：看服务端具体的额度、限额或访问错误；脚本不充值或转付费。
- 404 或模型列表缺少指定 ID：停止，不擅自改成相似模型。
- 429：免费配额或限流，保留错误后结束该次；不无限重试。
- 400：参数或响应格式不兼容，查看服务端消息。
- 502：上游生成失败，保留为服务错误。

原 Ollama 日志中的 `token repeat limit reached` 专项恢复仍只适用于 Ollama。新默认路径不再调用本地 Qwen。输出截断仍是无效输出，只保留脱敏且限长的首尾诊断片段，不能把截断内容当作答案。

## 验证边界

本次自动测试覆盖请求 URL/模型/认证、免费路由参数、模型列表和 tokenizer 发现、非免费或缺失模型拒绝、错误封装及完整引擎模拟流程。当前云环境未配置 OpenRouter key，未用真实 LFM 服务完成答题；不能把模拟测试报告为真实联网成功或准确率提升。Windows PowerShell 流程与实际供应商兼容性仍需本机运行确认。

## 模型 ID 不在目录时

用户于 2026-10-24（北京时间）返回的目录包含 `liquid/lfm-2.5-2.6b:free`，输入/输出价格均为 0；不带 `:free` 的 ID 未列出。原来的 30e5278 默认值漏掉此后缀，已修正。同期 `liquid/lfm-2.5-24b-a2b` 为付费模型，不作为替代。

可以在旧版本上直接指定正确 ID：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -Model liquid/lfm-2.5-2.6b:free
```

新版本遇到未列出的 ID 会在错误中列出同厂商当前公布的免费模型，不猜后缀或自动换模型。也可独立查询，无需输入 key、测试网页工具或发起模型推理：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -ListModels
```

公开模型目录能访问不等于 API key 已验证；认证与生成仍以实际 Chat Completions 响应为准。

## 规划器输出额度耗尽但没有正文

2026-10-24 的用户日志已经确认：正确模型 ID、零价格元数据、LiquidAI tokenizer 下载，以及一次真实 OpenRouter JSON 回答都成功。随后规划器连续两次返回 `finish_reason=length`、`completion_tokens=768`，正文为空。旧解析顺序先检查正文，误将截断主要报告为 `Missing text content`。这能确认生成额度耗尽，但不能单凭这份日志断定全部额度都消耗在推理上。

处理：

- OpenRouter 默认显式请求关闭可选推理，不读取推理文本作为答案。若服务仍提供推理字段，只记录其是否存在；服务提供 `usage.completion_tokens_details.reasoning_tokens` 时记录该数值，缺失时保留 null，不能视为 0。
- Windows 脚本新增 `-MaxOutputTokens`，默认 1024（范围 128–4096），verifier 保持 128。这是单次生成总额度，包含供应商可能计入的推理部分；与 512-token Capsule 存储预算分开。通用 Python 客户端仍以 `SCOPE_MAX_OUTPUT_TOKENS` 配置，未设置时保持旧默认 768。
- 优先判断 `finish_reason=length`，即使正文为空也明确报告截断；只有非截断的空正文才报告 Missing text content。日志和 usage 保存 finish reason、正文字符数、报告的推理 token 数、输出额度和请求推理模式，不保存推理正文。
- 默认仍是最多一次角色输出修正，不无限重试或自动放宽验证。`-ModelTimeout` 默认 120 秒，此次日志每次请求都在超时前返回，没有证据要求调整它。

更新后可运行：

```powershell
cd D:\qc
git -c http.proxy=http://127.0.0.1:7890 pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -SkipInstall -Reasoning disabled -MaxOutputTokens 1024
```

`-SkipInstall` 适用于前次已安装依赖的环境，本轮没有新增依赖。之前下载的 LFM tokenizer 会校验后复用。若仍有生成问题，应根据新日志的 `finish_reason`、`content_chars`、`reasoning_tokens` 和服务端错误判断；自动测试只覆盖协议与错误处理，不能保证远端供应商的实际行为。
