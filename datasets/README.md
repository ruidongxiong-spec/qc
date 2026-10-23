# SCOPE 论文相关公开数据：本地 CSV

这里包含真实公开问题/参考答案，不是生成的演示数据。CSV 已纳入仓库，正常 clone/下载代码包后即可使用，无需访问 Google Drive。文件采用 UTF-8 BOM + LF，可由 Excel 和本项目读取器加载。

**这些是公开开发集/镜像导出的 QA 视图，以及本项目自行固定的评测抽样；不是 SCOPE 作者提供的原始实验清单。** 论文没有给出两份 1,000 题的 ID、种子、划分和模型提示词，因此不能承诺精确复现论文表格。

| 文件 | 行数（不含表头） | 范围 |
|---|---:|---|
| hotpotqa_dev.csv | 7,405 | HotpotQA distractor 开发集，原始难度标签均为 hard |
| hotpotqa_sample1000.csv | 1,000 | 从上述开发集按稳定哈希选出；仍全部是 hard |
| musique_dev.csv | 2,417 | MuSiQue-Ans v1.0 开发集的镜像 QA 导出，含 2/3/4-hop |
| musique_2_3hop.csv | 2,012 | 排除 405 条 4-hop，保留 1,252 条 2-hop、760 条 3-hop |
| musique_sample1000.csv | 1,000 | 固定选取 500 条 2-hop + 500 条 3-hop |
| bamboogle.csv | 125 | Bamboogle 公开预发布版，保留可用第二答案 |
| scope_public_baseline2125.csv | 2,125 | 两份 1,000 题抽样 + Bamboogle 125 题，方便统一批测 |

各文件之间有重叠。请按一次实验选一个文件，不要把目录所有 CSV 直接拼接。合并版用于工程联调，正式论文应分别报告数据集、hop、难度结果。

## GitHub 来源及版本

下载时固定 Git commit，原文件大小、SHA-256、编码、路径和原始作者仓库见 [sources.json](sources.json)。导出 CSV 的行数、SHA-256 和组别分布见 [manifest.json](manifest.json)。

- **HotpotQA**：作者 [hotpotqa/hotpot](https://github.com/hotpotqa/hotpot) 发布数据说明；本次原 JSON 来自 [eduardokruel/mscthesis](https://github.com/eduardokruel/mscthesis/blob/2b996279ec7b13031d4ee064c9a41218991d79a7/datasets/hotpotqa/hotpot_dev_distractor_v1.json) 的固定版本镜像。保留原 `_id`、question、answer、level、type、supporting_facts。
- **MuSiQue**：作者 [StonyBrookNLP/musique](https://github.com/StonyBrookNLP/musique)；本次使用 [sonnguyen112/EvidencePath](https://github.com/sonnguyen112/EvidencePath/blob/db1a6bdd1b1d818e578ab1ab2c0a9e41ac0a9d15/artifacts/musique-ans/questions.jsonl) 已规范化的 2,417 题导出。原始 raw JSONL 使用 Git LFS，本次未成功下载 LFS 对象，不能将此导出描述为与官方原始 JSONL 字节相同。保留 question_id、question、可用 answers、support_units；未提供的原始 answer_aliases/分解信息无法补造。hops 从保留的官方 ID 前缀提取。
- **Bamboogle**：作者 [ofirpress/self-ask](https://github.com/ofirpress/self-ask/blob/main/datasets/bamboogle.md) 指向原始表格；GitHub CSV 镜像来自 [sunlab-osu/Understanding-CoT](https://github.com/sunlab-osu/Understanding-CoT/blob/f03070c45dace69892fc594dfb3cf6c2afc657f9/Bamboogle%20Prerelease%20-%20Sheet1.csv)。原 CSV 为 Windows-1252 编码，转为 UTF-8。原文件无 ID，`bamboogle-001` 等由原始行号生成，不是作者原始 ID。

作者分发端点（CMU / Google Drive / Google Sheets）在本云环境下受到出口策略限制。因此使用以上明确标记的 GitHub 镜像，未把镜像内容宣称为已与官方分发文件独立逐字核验。固定 SHA-256 可验证本次快照的重复获取完整性，不能替代官方来源认证。

## 字段与使用

列：`id,dataset,split,question,answer,answer_aliases_json,level,hops,question_type,supporting_facts_json`。

空的 level/hops 表示来源没有明确标注；不推测、不补造。HotpotQA 不强制填 `hops=2`，因为数据文件没有逐题该字段。MuSiQue support_units 为镜像文档 ID/段落引用，与 HotpotQA 的 `[title,sentence_id]` 不是同一格式。`supporting_facts_json` 用于审计，不是统一的支持事实评测格式。

CSV 是问题/答案及元数据视图，**不包含原数据的全部 context/documents 正文**，也不能替代官方训练集。当前系统通过公开工具检索证据；只把 question 交给 Agent，answer/aliases 用于运行结束后的评分，避免答案泄漏。

```bash
# 先确认模型、密钥、工具网络已配置；以下会实际调用模型。
python -m scope.cli evaluate --input datasets/bamboogle.csv --limit 5 --runs 1 --output runs/bamboogle-smoke.jsonl
python -m scope.benchmark --models examples/models.json --input datasets/scope_public_baseline2125.csv --output runs/public-baseline --runs 3
```

模型矩阵需先替换占位 model ID。当前未对这些数据调用真实模型，也不报告准确率。已有 JSONL 输入仍兼容。

## 可重复准备与抽样

```bash
python scripts/prepare_datasets.py          # 固定 GitHub URL，校验原始字节数/SHA-256，导出
python scripts/prepare_datasets.py --offline # 使用已校验 raw 缓存，不访问网络
```

`raw/` 为本地生成缓存，不进 Git，下载代码包也不含这约 100 MB 原始缓存。CSV 已经存在，运行评测无需重新准备。脚本拒绝覆盖内容不同的已有文件，也不会使用校验失败的下载。依赖只需 Python 标准库。

抽样顺序为 `SHA256("scope-public-baseline-v1-seed42:" + id)` 升序，各组取前 N；MuSiQue 分组各取 500。这个种子和方案是本项目定义的，不是论文给出的。manifest 固定输出散列；`.gitattributes` 固定 LF，防止 Windows 换行转换改变数据快照。

## 许可与引用

原数据权利不转移给项目代码许可，CSV 的选列、格式转换和子集选择属于本项目整理：

- HotpotQA：作者说明为 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)，相应导出/子集继续遵循该条款，注明原作者与转换。
- MuSiQue：作者说明为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，保留出处并注明镜像转换。
- Bamboogle：作者说明为 MIT，保留 [原始许可文本](licenses/Bamboogle-MIT.txt)。

混合 CSV 同时保留各行所属数据集的相应许可；不能把混合数据统一重标为宽松的代码许可。

研究引用：Yang et al., *HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering*, EMNLP 2018；Trivedi et al., *MuSiQue: Multihop Questions via Single-hop Question Composition*, TACL 2022；Press et al., *Measuring and Narrowing the Compositionality Gap in Language Models*, Findings of EMNLP 2023。
