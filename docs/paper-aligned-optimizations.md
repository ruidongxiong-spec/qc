# 基于论文的输出与上下文优化

目标：针对本地 Qwen2.5:7b 出现的错误 JSON 结构、长网页抽取失败、重复访问和 Capsule 预算失真，降低格式失败与无效调用。保持 DAG、节点隔离、直接父节点注入、独立验证和默认每节点 8 步。改动没有预置任何数据集答案，也不以联网检查通过或正常退出代替答题正确。

## 与 PDF 的对应关系

| PDF 位置 | 机制 | 当前实现与边界 |
| --- | --- | --- |
| §3.1，第 4 页，式 (1) | 全局记忆只含 Pass Capsule | 保留独立验证；来源还必须属于工具观察或已验证父节点。格式合法不表示事实正确。 |
| §3.2，第 5 页 | 局部状态压缩为 findings、missing_info、new_leads | 原始观察先分段抽取，事实去重追加；仅未解决信息与未访问链接留在对应列表。执行器默认不接收完整网页历史。 |
| §3.2，第 5–6 页 | Pass 提交，Uncertain 反馈，Rejected 负约束 | 已有发现且连续两次重复成功动作，或剩最后一步时，调度 Finish 候选；仍须经过相同验证。此调度是工程扩展，可能减少最后补充检索的机会。 |
| §4.1，第 7 页 | 每步至多一个工具调用，最多 8 步，512-token Capsule | 工具和步骤预算保留；Capsule 改为指定 tokenizer 对完整 JSON 的 token 计数。分段提取会增加模型调用数，不增加工具步数。 |
| §4.1，第 7 页 | 验证日志保留末尾 15,000 字符 | 保留此截断规则，另有状态和候选；较早原始证据可能不在日志尾部。 |

论文没有公开完整提示词、tokenizer 文件、Capsule 序列化规则或观察分段算法。以下 Schema、分段长度、重叠、8192 上下文和输出限额均是本项目的工程选择。Qwen2.5:7b + DDGS 也不同于论文的 Qwen3 系列 + Serper，不能据此声称复现论文准确率。

论文式 (1) 与算法第 28 行的 ForcedSeal 传播存在表述冲突。本项目延续严格门控：未验证部分结果存入 partials，依赖它的节点 blocked；最终合成只使用已验证记忆。不会为了提高完成率把部分结果当作 Pass。

## 输出约束

Windows 脚本默认调用 Ollama `/api/chat`，将角色 JSON Schema 交给 `format`。各角色固定必需字段，动作和验证结论使用枚举；提取器来源限于当前观察分段，候选来源限于已观察来源。工具动作中不适用的字段用 null/空列表，执行前归一化，避免相同调用因多余字段绕过去重。

仍执行 DAG、参数、来源、数值、状态大小和 Capsule 预算检查。模型返回格式错误时，默认最多一次修正；执行器错误消耗节点步骤。Schema 不替代 verifier 的事实检查，也不保证模型不会误读网页。

通用 OpenAI 兼容客户端保留原来的 `json_object` 模式。支持严格 Schema 的端点可设置 `SCOPE_RESPONSE_FORMAT=json_schema`。不支持时会明确报错，不静默降级。

## 上下文处理

- 默认每段工具正文最多 4000 字符，单个长结果相邻段最多重叠 128 字符（小段上限为段长的 1/8）。来源、偏移和原始正文长度随段保留；短搜索结果可合并到一段。处理工具已返回的全部文字，包括尾部。读取器原有的 20,000 字符限制仍存在，分段不代表获取网站全文。
- 分段提取只保存与当前节点相关的事实；上一段压缩状态参与下一段。某段两次输出都无效时记录失败，继续处理后续段，已成功的状态保留。只有所有分段成功才将工具动作标记为完成，因此仍允许再次尝试未完成抽取。
- 访问过的 URL 从新线索中移除。执行器接收紧凑状态、成功动作、最近失败动作和来源列表；原始观察留在本节点验证日志。原有 `no-memory` 消融仍会注入历史，不能与默认模式混为一谈。
- 状态上限仍为 12,000 字符；默认输出限额 768 tokens，verifier 128 tokens。超限要求模型缩短或显式失败，不无声裁掉证据。复杂规划可能需要调高 `SCOPE_MAX_OUTPUT_TOKENS`，范围 128–4096。
- Ollama `num_ctx` 默认 8192，可通过 `-ContextTokens` 配置。指定 tokenizer 后，对序列化消息计数，再预留 512 tokens 给聊天模板/control 和配置的输出空间；超出就明确报错。此输入计数是预算预检查，不是 Ollama 聊天模板的精确 prompt token 数；实际使用量读取服务返回的 `prompt_eval_count` / `eval_count`。更大上下文会增加内存/显存需求。

分段增加调用和延迟，也可能丢失跨越很远位置的语义关系；重叠仅减少边界问题。append-only 状态超限时不会删除既有事实，仍可能留下 unresolved。需要用真实样本衡量收益，不能预先保证准确率提升。

## 512 tokens 与可追溯性

`tokenizers==0.22.1` 加载指定 tokenizer.json，关闭文件内的截断和 padding，对整个 Capsule JSON（含节点 ID、状态、rationale）计数。Windows 默认模型 `qwen2.5:7b` 对应 `Qwen/Qwen2.5-7B-Instruct` tokenizer；首次解析仓库 revision，再按不可变 revision 下载文件，记录 revision 和 SHA-256，后续离线核验缓存。它是对应模型家族的 tokenizer；自定义 Ollama 模型必须核对其实际 tokenizer，不能仅凭名称保证与自定义 GGUF 完全一致。

`-TokenizerPath` 可直接使用本机匹配文件。更换模型时必须指定路径或 `-TokenizerRepo owner/model`。下载失败时先检查代理，或另行准备文件；不会偷偷退回字节预算。`-LegacyByteBudget` 是显式兼容选项，使用旧 512 UTF-8 字节计数，结果标记为 `utf8_bytes_legacy`。通用 CLI 未提供 `--tokenizer` 时也保留旧模式。

结果记录预算单位、tokenizer 哈希、分段长度、模型 API/Schema 模式和上下文设置；分段事件记录新增事实数。矩阵运行的恢复指纹包含模型设置及 tokenizer 内容哈希，避免不同配置的结果接续在一起。

## Windows 运行

保持 Clash 与 Ollama 运行，在 PowerShell 执行：

```powershell
cd D:\qc
git -c http.proxy=http://127.0.0.1:7890 pull --ff-only origin main
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -SearchBackend auto
```

已有模型无需 `-PullModel`；升级后第一次不要加 `-SkipInstall`，以便安装 tokenizers。首次下载 tokenizer.json 不会重新下载模型。已有 tokenizer 可用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -TokenizerPath D:\models\qwen2.5-tokenizer.json
```

每次运行仍只评测指定 CSV 的第一题一次。查看脚本打印的 `.log`、`.jsonl` 与 `.summary.json`：先检查 status/outcome，再看 answer/reference、EM/F1、每节点验证与错误事件。首轮应核对 Schema 请求、tokenizer 哈希、分段进度、实际模型 token 使用量和最终答案。

## 验证与后续比较

新增离线测试覆盖原生 Ollama HTTP 协议和 Schema、OpenAI 严格 Schema、完整 token 计数、artifact 版本固定与缓存校验、长正文尾部/边界保留、JSON-LD 错误后续恢复、来源限定、重复动作后的验证门控、上下文超限前置报错。

```bash
python -m pip install -e '.[free-web,token-budget]'
python -m unittest discover -s tests -v
```

本次云环境不具备用户 Windows 的 Ollama 服务，且到 Hugging Face 的请求被网络策略拒绝；下载流程通过模拟响应测试，token 计数通过真实 tokenizers 库和测试 tokenizer 验证。尚未在此环境实际下载官方 Qwen tokenizer，也未完成真实 Qwen/DDGS/Jina 端到端验证。PowerShell 启动流程仍需用户本机确认。

下一步先验证一题；再固定一小批 HotpotQA/MuSiQue 样本、模型、检索缓存和 tokenizer，比较旧版本与本版本的 EM/F1、拒答率、格式失败、重复动作、模型调用数、token 使用和耗时。至少三次运行。只有数据支持后才能判断是否改善模型表现；本轮回归测试只验证机制与故障处理。
