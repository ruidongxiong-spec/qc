# MuSiQue 完整数据与 1,000 题抽样

更新：2026-10-23。当前评测文件为 `datasets/musique_ans_dev_sample1000.csv`：**2-hop 600 条、3-hop 400 条，共 1,000 条**。只从有答案的 MuSiQue-Ans v1.0 dev 抽取，不含 train、test、4-hop 或 Full 的不可回答上下文。逐题语义审阅按用户要求暂停，没有排除或改写题目。

## 作者原始来源

作者仓库：[StonyBrookNLP/musique](https://github.com/StonyBrookNLP/musique)，下载说明核对版本 `922ac98f19a201998dbdae6d7f2887a5258dbdeb`。已直接下载作者指定的 [v1.0 Google Drive ZIP](https://drive.google.com/file/d/1tGdADlNjWFaHLeZZGShh2IRcpO6Lv24h/view?usp=sharing)，而非仅有问题/答案的二次镜像。

ZIP 为 272,049,578 bytes，SHA-256：

```text
98f839bf2fd5319f5c688aed77901a6d5c30b3b9f9f691ab9a8ecafb045ee0cd
```

每个原 JSONL 的字节数、SHA-256、行数和 hop 分布见 `datasets/musique_sources.json`；每个输出 CSV 的散列见 `datasets/musique_manifest.json`。完整 ZIP 包括六份数据及原始单跳种子问题清单；后者也导出为 `datasets/official/musique_dev_test_singlehop_questions.csv`。ZIP 内 `.DS_Store` 为系统元数据，不导出。

## 完整 CSV

| 原始数据 | 条数 | 完整原字段 CSV 目录 |
|---|---:|---|
| MuSiQue-Ans train | 19,938 | `datasets/official/musique_ans/train/` |
| MuSiQue-Ans dev | 2,417 | `datasets/official/musique_ans/dev/` |
| MuSiQue-Ans test | 2,459 | `datasets/official/musique_ans/test/` |
| MuSiQue-Full train | 39,876 | `datasets/official/musique_full/train/` |
| MuSiQue-Full dev | 4,834 | `datasets/official/musique_full/dev/` |
| MuSiQue-Full test | 4,918 | `datasets/official/musique_full/test/` |

每个目录中的 `part-*.csv` 合起来是该划分的全部记录，共 **45 个分片**，单片不超过 20 MiB，均已纳入 Git，正常 clone 即有。分片仅为符合 GitHub 文件限制，不代表新的数据划分。

完整字段列：`id,question,answer,answer_aliases_json,answerable_json,paragraphs_json,question_decomposition_json,missing_fields_json`。嵌套字段 JSON 编码，保留段落全文、索引、标题、支持标签、问题分解和别名；`missing_fields_json` 记录原本不存在的字段，可区分缺失、null、空字符串。

官方 test 原本只有 `id,question,paragraphs`，不补造答案、answerable 或支持标签。MuSiQue-Full 有意为每个问题提供可回答和不可回答上下文版本，因此同一 ID 可出现两次；不能按 ID 去重丢掉一半，也不能用普通有答案 QA 评分器直接评分 Full。原数据顺序保留。

## 轻量 QA 与当前抽样

| 文件 | 条数 | 内容 |
|---|---:|---|
| `datasets/musique_ans_train.csv` | 19,938 | Ans train 全部 QA，不含段落正文 |
| `datasets/musique_ans_dev.csv` | 2,417 | Ans dev 全部 QA，2-hop 1,252 / 3-hop 760 / 4-hop 405 |
| `datasets/musique_ans_test.csv` | 2,459 | Ans test 全部问题；无答案，不能直接评分 |
| `datasets/musique_ans_dev_2_3hop.csv` | 2,012 | Ans dev 排除 4-hop 后的完整候选池 |
| `datasets/musique_ans_dev_sample1000.csv` | 1,000 | 当前固定抽样：2-hop 600 / 3-hop 400 |
| `datasets/scope_selected2125.csv` | 2,125 | HotpotQA 500/300/200 + 本次 MuSiQue 600/400 + 原 Bamboogle 125 |

QA 列与现有评测读取器一致，包含原始 answer 和 answer_aliases；MuSiQue 的 `supporting_facts_json` 是支持段落的 `{idx,title}` 列表，不能按 HotpotQA 的句级标注评分。只有 question 传给 Agent，参考答案只用于结束后评分。

抽样种子为 `scope-musique-ans-dev-v2-seed42`。在每个 hop 内按 `SHA256(seed + ":" + 原始ID)` 升序取前 600 或 400，哈希相同时按 ID 排序。它是可复现的伪随机分层抽样，输入行顺序改变不影响结果。hop 同时核对原 ID 前缀与分解长度。该配额和种子由本项目定义，论文没有公开可核对的逐题 ID 清单，因此不能称为作者精确子集。

旧 `musique_dev.csv`、`musique_2_3hop.csv`、`musique_sample1000.csv` 保留为镜像 QA 历史基线；旧抽样仍为 500/500。`scope_public_baseline2125.csv` 也是旧基线。不要把新旧 CSV 拼接为一个评测集。当前合并版来源及 SHA-256 在 `datasets/selection_manifest.json`，HotpotQA 和 Bamboogle 源文件未改动。

## 重建与运行

已有 CSV 的评测不需要重新下载数据，重建只需 Python 标准库：

```bash
python scripts/export_musique.py           # 获取并校验作者 ZIP，导出全部完整 CSV 和抽样
python scripts/export_musique.py --offline # 使用已校验 ZIP 和原文件缓存
python scripts/build_selected_dataset.py   # 校验配额，重建当前合并版；拒绝覆盖不同快照
python -m unittest discover -s tests -v
```

明确切换合并快照时使用 `python scripts/build_selected_dataset.py --update`；它不会修改三份输入 CSV。HotpotQA 重建脚本现在只管理 HotpotQA 输出，不会把 MuSiQue 重置成旧 500/500。原 ZIP/JSONL 缓存在被 Git 忽略的 `datasets/raw/`，CSV 均在 Git 内。

模型配置完成后可运行：

```bash
python -m scope.benchmark --models examples/models.json \
  --input datasets/musique_ans_dev_sample1000.csv --output runs/musique --runs 3
```

当前尚未生成真实模型性能结果。Windows 用户更新干净的 `D:\qc` Git 副本即可得到同一批 CSV；云环境不能直接写用户电脑 D 盘。

许可：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。引用：Trivedi et al., *MuSiQue: Multihop Questions via Single-hop Question Composition*, TACL 2022。本项目转换格式并抽样，不改原始题意；数据许可独立于代码许可。
