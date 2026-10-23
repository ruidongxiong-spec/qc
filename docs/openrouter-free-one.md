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
