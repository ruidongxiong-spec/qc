# 单条免费评测的运行可靠性方案

目标是让 DDGS + Jina + OpenRouter（可选本地 Ollama） 的真实执行可诊断、可保存、可复测。流程完成、得出部分结论、证据不足和运行失败必须可区分；联网成功不等于正确回答，测试通过也不代表本机实时服务已验证。

## 执行顺序与预算

1. Windows 脚本创建唯一运行前缀，立即打印日志和结果路径、模型、搜索配置、步数、修正次数、代码 commit。依赖安装可通过 `-SkipInstall` 跳过。
2. 用新缓存验证搜索、Jina 读取和搜索到读取；不通过则停止预检查，在 `.log` 记录阶段，不伪造题目结果。可显式使用 `-SkipWebCheck` 跳过这一步；正式题目仍访问真实工具或已有缓存。
3. 默认检查 OpenRouter 模型免费价格、对应 tokenizer，再验证模型 JSON 对象和非空答案格式；Ollama 为显式可选模式。默认请求超时 120 秒，可用 `-ModelTimeout` 设置；不是整题超时，多次调用可能累计运行数分钟。
4. 读取所选 CSV 第一条，不向模型提供参考答案。按规划→执行→提取→验证→汇总执行，并在结束后计算 EM/F1。
5. 每条尝试写 JSONL 并刷新，再原子更新 summary。批量模式的单条运行失败不会丢弃其他样本；全部处理完后，只要有运行失败，退出码为 1。可处理的 Ctrl+C 保存当前失败记录后退出 130；强制结束进程、磁盘不可写等情况不能保证最后一条落盘。

默认每节点最多 8 步、最多 32 个节点。模型格式/约束修正默认额外 1 次（`-OutputRetries` 或 `--output-retries`，范围 0–3）。修正请求携带具体错误和截断的上一次输出，温度降至 0；修正消耗真实模型调用，不能把它当成免费计算。执行器错误不叠加额外调用预算，直接消耗下一步。模型请求超时或一般 HTTP 失败不自动重试。仅 Ollama 明确返回 HTTP 500 且完整消息为 `prediction aborted, token repeat limit reached` 时，归类为生成错误，在已有输出/步骤预算内恢复；不套用于 OpenRouter 的 429/502 等服务错误。

## 故障处理矩阵

| 阶段 | 校验/故障 | 处理 |
| --- | --- | --- |
| 模型 HTTP | 断连、超时、非成功 HTTP | 明确标记模型传输错误，结束该条，保存错误阶段及已完成过程；不会变成“证据不足” |
| 模型响应 | 无 choices、空 content、null、非 JSON、非对象、截断响应 | 标记输出错误，按角色修正预算处理 |
| 规划 | 空计划、重复 ID、缺失依赖、自依赖、环路、字段类型错误、节点超限 | 将校验错误反馈模型修正；仍无效则运行失败；不悄悄改成无 DAG 或删除依赖 |
| 执行 | 未知动作、arguments 类型不对、缺少 query/url/expression | 将错误反馈给执行器，消耗原有步数 |
| 检索 | 空结果、网络失败、HTTP 错误 | 记录工具失败及底层错误，反馈执行器换查询/网页；不自动切换配置的服务 |
| 缓存 | 内容损坏或身份不匹配 | 当作未命中重新获取；新请求成功才覆盖缓存 |
| 提取 | 错误字段、无效来源、状态超限 | 使用同一 observation 请求修正，不重新访问网页；仍失败可在后续步数重试，未完成提取的动作不会被标为成功重复 |
| 验证 | 非法 verdict、空或过长 reason | 请求修正；拒绝/不确定反馈给执行器；只有通过验证且引用已观察来源的结果进入记忆 |
| Capsule | 超出配置预算 | 显示实际 token 数（或显式 legacy 字节数）与上限，让执行器缩短；部分提取仍失败则记 partial_error |
| 重复动作 | 工具调用和内容提取均成功后重复相同动作/参数 | 不重复访问或提取，反馈选择新线索或完成；仍消耗执行步数 |
| 依赖未完成 | 上游没有已验证结果 | 下游明确 blocked；不传播未经验证的部分结果 |
| 汇总 | 空答案、错误 JSON/类型 | 请求修正；仍失败则记 synthesis_error 并输出 Insufficient evidence |
| 无已验证事实 | 汇总模型给出猜测 | 固定明确弃答，不把猜测当作已验证答案 |
| 意外程序错误 | 未预期的异常 | 运行失败，保存异常类型、阶段、堆栈及当前快照，不能吞掉后算成功 |

Windows 脚本现在默认使用对应 tokenizer 的 **512 tokens** 预算；首次准备 tokenizer.json，后续核验本地缓存的 SHA-256。`-LegacyByteBudget` 可显式重现旧的 512 UTF-8 字节模式，日志与结果会标记单位，二者不能混用。通用 CLI 通过 `--tokenizer` 指定文件。原生 Ollama Schema 解码、分段提取、上下文预算及验证调度详见 [机制与优化](paper-aligned-optimizations.md)。正式比较应冻结代码、模型、tokenizer 和配置。

## 状态、评分和退出码

JSONL 的 `status` 为 `completed` 或 `error`。`completed` 记录的 `result.outcome`：

- `answered`：所有规划节点均有已验证结果并生成答案。它不是答案正确性标签，仍需看 EM/F1。
- `partial`：只完成部分节点并生成受已有证据约束的答案。
- `abstained`：明确输出 `Insufficient evidence`。

`error` 记录包含错误阶段、类型、脱敏消息、堆栈、已有计划/记忆/事件、用量和检索审计，`metrics` 为 null，不伪造一个正确或错误的模型答案。summary 的 EM/F1 使用全部 attempted 作为分母，运行失败贡献 0，并同时报告 failed 数量；completed 包含已完成的弃答。summary 的 expected/attempted/completed 应一起看。发生运行错误退出 1，只有 answered/partial/abstained 且没有运行错误时退出 0。

工具成功日志仅表示拿到了内容，不保证网页事实正确。参考答案只用于模型执行后的本地评分；如显式启用独立 judge，则另行发送给 judge。

## 日志与复测

日志包含阶段、子任务、步骤、模型角色、请求与修正次数、错误原因、截断的无效输出、缓存状态、返回长度、验证和保存状态。`START/WAIT/DONE/FAIL` 表示调用进度。不会输出完整提示词；配置的 API key 和 URL 中的代理认证信息会脱敏。Windows 脚本仅显示 key 是否存在。日志可能包含题目和访问链接，存放在 git 忽略的 runs 目录。

```powershell
cd D:\qc
git -c http.proxy=http://127.0.0.1:7890 pull --ff-only origin main
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -SearchBackend auto
# 后续依赖已安装时
powershell -ExecutionPolicy Bypass -File .\scripts\run-free-one.ps1 -SkipInstall
```

无需手工重定向即可获得 `.log`。结果文件同一前缀：`.jsonl` 和 `.summary.json`。预检查失败可能只生成 `.log`，此时没有实际评测。不要反复读取前一次的日志。

自动测试覆盖实际报告的缺失依赖、重复动作、超长部分结果、空汇总，以及非法图、模型响应结构、工具格式、超时、证据来源、缓存损坏、长请求日志、失败落盘和后续样本继续执行。离线故障注入与本地 HTTP 模拟服务测试不等价于 Windows 上的真实 Ollama/DDGS/Jina 成功运行；最终需要本机实际输出确认。

## Ollama 返回 HTTP 500 时

HTTP 500 表示模型服务返回了内部错误，不能仅凭状态码判定为内存不足或上下文超限。模型客户端会记录请求字符数/字节数，并保留服务端 JSON 中最多 1200 字符的错误消息（API key 脱敏）；不会打印完整请求或任意 HTML 错误页。此类错误仍记录为运行失败，不自动重试。

Windows 上可先查看服务日志，无需重跑整题：

```powershell
Get-Content "$env:LOCALAPPDATA\Ollama\server.log" -Tail 80
ollama ps
```

根据具体服务日志再决定是否调整模型、上下文或硬件资源。20,000 字符的网页并不等于 20,000 tokens，不能从截断字符数直接推断是否超出了模型上下文。

## 生成重复与模型切换

用户在 bfd4aaa 的本机日志中已经成功运行 DDGS/Jina、下载 Qwen tokenizer，并通过 Ollama JSON 检查；随后执行器先 `finish_reason=length`，再收到 Ollama 的 token repeat abort。该日志能确认输出截断及重复保护触发，不能确定哪个字段导致循环。

输出契约 v2 移除执行器嵌套 nullable 分支，不适用字段使用空字符串/空列表/0；confidence 使用 0 到 1、间隔 0.1 的枚举。这是解码工程选择，不是论文规定或置信度校准。发生输出错误后的下一执行步骤采用温度 0，不额外增加步数。截断/非 JSON 输出记录脱敏后的有限首尾片段，截断内容绝不当作有效答案。

当前用户选择 OpenRouter LFM 模型作为默认。该路径不使用本地 Ollama 的采样器；本地重复错误的专项恢复仅用于显式 Ollama 模式。见 [OpenRouter 接入](openrouter-free-one.md)。
