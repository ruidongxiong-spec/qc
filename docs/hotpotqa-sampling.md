# HotpotQA 数据口径与固定抽样

用户已确认使用官方 `hotpotqa/hotpot_qa` 的 **fullwiki / train**，按原始标签随机抽取 **easy 500 / medium 300 / hard 200**，共 1,000。最新要求是**暂停逐题审阅，先交付完整 CSV 和固定抽样**，因此当前不应用排除建议，也不发布清理完成版。

## 已下载的官方版本

固定 revision：`1908d6afbbead072334abe2965f91bd2709910ab`。四个原始 Parquet 均核对官方仓库 LFS SHA-256 和字节数。实际 train 计数为 easy **17,972**、medium **56,814**、hard **15,661**，总计 90,447；以本版本实测为准。

| 路径（相对 datasets/） | 内容 |
|---|---|
| hotpotqa_fullwiki_train.csv | 全部 90,447 行 QA/元数据视图 |
| hotpotqa_fullwiki_validation.csv | 全部 7,405 行、有答案、全 hard |
| hotpotqa_fullwiki_test.csv | 全部 7,405 行，无公开参考答案 |
| official/hotpotqa_fullwiki/train/part-*.csv | 完整原字段，含 context，28 片 |
| official/hotpotqa_fullwiki/validation/part-*.csv | 完整原字段，3 片 |
| official/hotpotqa_fullwiki/test/part-*.csv | 完整原字段，3 片 |
| hotpotqa_train_sample1000.csv | 新的 500/300/200 固定随机初抽 |
| scope_selected2125.csv | 新 HotpotQA 1,000 + 官方 MuSiQue-Ans dev 1,000（600/400）+ 原 Bamboogle 125 |
| hotpotqa_sample1000.csv | 旧 dev hard 1,000 基线，保留 |
| hotpotqa_fullwiki_manifest.json | 所有新导出文件的数量、大小及 SHA-256 |

完整字段共约 634 MiB，每片最多 20 MiB，以满足 GitHub 单文件限制。原始 `id/question/answer/type/level` 保持，`supporting_facts/context` 使用 JSON 列；`null_fields_json` 区分 null 和空字符串，可恢复原记录。CSV 为 UTF-8 BOM + LF，不改写题干和答案。根目录 QA 视图省去 context，便于日常评分。

**官方 test 的 answer、level、type 未公开，不补造。** 当前评分 CLI 要求参考答案，应使用抽样或 validation，不能对无答案 test 计算 EM/F1。

## 实验口径

两种配置共享官方训练源，`distractor/fullwiki` 是检索设置，`easy/medium/hard` 是题目难度。官方 dev（HF `validation`）只有 hard；ReAct 的 500 条随机 dev 也没有三难度。

500/300/200 是本项目的**自定义难度分层评测集**，保留 `split=train`，不是官方 test，也不是 SCOPE 作者公开的原始 ID 清单。当前 Agent 只接收 question 并通过网页工具取证；下载 fullwiki 配置不等于已搭建固定 Wikipedia 语料的官方 fullwiki 检索评测。

## 重新导出

已有 CSV 已入 Git，正常 clone 无需再下载 Parquet。只有重建时需要额外依赖：

```bash
python -m pip install -e '.[dataset-prep]'
python scripts/export_official_hotpotqa.py
# 已有四个校验通过的缓存时：
python scripts/export_official_hotpotqa.py --offline
```

四个 Parquet 总计约 369 MiB，缓存于被 Git 忽略的 `datasets/raw/hotpotqa_fullwiki_train/`。脚本使用固定 revision URL 并校验散列；来源见 `datasets/hotpotqa_fullwiki_sources.json`。

种子为 `scope-hotpotqa-train-v1-seed42`。每组按 `SHA256(seed + ':' + original_id)` 排序取配额，实现与输入顺序无关的确定性伪随机抽样。不同内容的现有导出会触发报错，避免悄悄改动已冻结数据。Bamboogle 文件与新合并版中对应的行值、题序均不变。

## 暂停的审阅

`datasets/hotpotqa_train_review/` 保留初抽、每难度额外 200 条候选、来源绑定及首轮阅读记录。初抽 1,000 和额外 260 候选已做首轮题意阅读，但仍有待支持句核验的备注。**这是助手的判断，不是独立人工标注或全部答案事实核验，未用于当前抽样。**

用户恢复后，需复核现有建议，只因语义、前提和唯一性问题剔除，不能因题难或模型答错剔除。按同一 train、同一难度补足，保留原 question/answer，记录 old/new ID 与理由，另发清理版。

```bash
python scripts/prepare_hotpotqa_train.py prepare --offline
# 完成并复核 reviews.json 后才运行；当前暂停，不执行：
python scripts/prepare_hotpotqa_train.py finalize
```

冻结逻辑拒绝未审阅题、无理由决定、未知 ID、重复题、来源/种子绑定不符及不足配额。但现有 keep 仍含待事实核查备注，所以逻辑可冻结不代表语义质量已通过，当前没有执行冻结。

参考：[官方划分](https://github.com/hotpotqa/hotpot#data-download-and-preprocessing)、[官方 HF 仓库](https://huggingface.co/datasets/hotpotqa/hotpot_qa)、[两配置共用训练源的 HF 历史脚本](https://github.com/huggingface/datasets/blob/1.18.3/datasets/hotpot_qa/hotpot_qa.py)、[ReAct](https://github.com/ysymyth/ReAct)。Yang et al., EMNLP 2018；数据许可 CC BY-SA 4.0，保留作者、来源及转换说明。

合并输入现由 `python scripts/build_selected_dataset.py` 单独重建；明确变更版本时使用 `--update`。HotpotQA 导出脚本不再覆盖合并版或重置 MuSiQue 配额。见 `datasets/selection_manifest.json`。
