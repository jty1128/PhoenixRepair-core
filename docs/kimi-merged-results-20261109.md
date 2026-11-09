# Kimi K2.7 Code：补跑替换后的 HARD-51 详细统计

统计日期：2026-11-09。成绩为 **Sample 1：29/51；Sample 2：33/51；Sample 3：33/51；TTS Final：34/51**。本页只统计替换后保留的运行，被替换的旧运行不重复计入。每 task 均除以完整的 51 题，失败题也计入分母与消耗。

每题 Token 与分项美元费用见 [README](../README.md#kimi-k27-code实验消耗补跑替换后2026-11-09)。可下载的精确聚合数据为 [JSON](../results/kimi_hard51_merged_usage_20261109.json) 和 [CSV](../results/kimi_hard51_merged_usage_20261109.csv)；CSV 同时包含总量和每题平均值。费用保留计算精度后再四舍五入，显示值相加可能有末位差异。

## 替换规则与成绩核对

同一题同一 Sample，优先使用指定的最新 2h 补跑，其次为本轮扩大上下文的补跑，再其次为原始运行。替换不依据评测是否通过。Final 使用受影响的 23 题新结果，另外 28 题保留此前已选定的 Final。

| 实验 | 保留原始运行 | 保留上下文补跑 | 保留本轮 2h 补跑 | 合计 | 原成绩 → 合并成绩 | 新增通过 | 丢失通过 |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| Sample 1 | 33 | 17 | 1 | 51 | 26 → 29 | 3 | 0 |
| Sample 2 | 34 | 13 | 4 | 51 | 36 → 33 | 1 | 4 |
| Sample 3 | 35 | 13 | 3 | 51 | 32 → 33 | 4 | 3 |
| TTS Final | 28 | 0 | 23 | 51 | 34 → 34 | 4 | 4 |

Final 的 28 题保留结果由 25 题原始 Final 和 3 题此前 retry8 结果组成。前三个 Sample 共替换 51 条样本，涉及 23 道不同题目；这 23 题重新执行 summary 和 Final。其余 28 题保留原 summary 与 Final。先前 retry8 中未保留的尝试不进入本表。

这次核对覆盖四组各 51 题，共 **204 条结果**：

- 各组题目集合一致；逐题核对 `final.patch` 的 SHA-256、选定记录与对应评测的补丁哈希。
- 对应评测均有明确的 resolved/unresolved 状态；已记录的 apply 返回码为 0。
- 23 条新 Final 的 candidate provenance 与合并后的三个 Sample 补丁逐一匹配，共 69 个候选哈希。
- 原始生成用量从 `kimi_requests.jsonl` 的 provider usage 重算；补跑用量从独立 SQLite 账本重算。通过 attempt 的 process/request 记录把 PID 映射到题目，不能按账本中共同的 `task_id=author` 合并题目。
- 所选运行内部已记录的恢复尝试计入该运行；同题被替换掉的旧运行整段排除。评分不用于挑选费用较低或成绩更高的尝试。

## 所选生成阶段的总 Token

下面均为 **51 题总量**。Final 行只表示最终生成阶段，便于与三个 Sample 分开核对；它不是 README 中包含完整流程的 TTS Final 行。

| 阶段 | 总 tokens | Input（含缓存） | 缓存 input | 非缓存 input | Output |
| --- | ---: | ---: | ---: | ---: | ---: |
| sample-1 | 280,912,427 | 278,824,585 | 268,942,448 | 9,882,137 | 2,087,842 |
| sample-2 | 289,181,800 | 286,980,684 | 276,602,298 | 10,378,386 | 2,201,116 |
| sample-3 | 290,243,615 | 288,094,316 | 278,071,469 | 10,022,847 | 2,149,299 |
| final | 272,267,664 | 270,139,920 | 259,724,084 | 10,415,836 | 2,127,744 |

Input 已包含缓存命中；非缓存 input = input − 缓存 input；总 tokens = input + output。Output 包含 provider 返回的 thinking/reasoning tokens，不再次叠加 reasoning。

## TTS 完整流程如何计算

| 组成 | 已记录总 tokens | 统计方式 |
| --- | ---: | --- |
| 合并后的 Sample 1 | 280,912,427 | 51 题所选运行 |
| 合并后的 Sample 2 | 289,181,800 | 51 题所选运行 |
| 合并后的 Sample 3 | 290,243,615 | 51 题所选运行 |
| 合并后的 Final 生成 | 272,267,664 | 23 题新 Final + 28 题保留 Final |
| 23 题新 summary | 773,126 | Input 529,173 + Output 243,953，缓存命中 0 |
| 28 题保留的旧 summary | 547,299 | Input 249,413 + Output 297,886，缓存命中 26,752 |
| **完整 TTS 实际记录用量** | **1,133,925,931** | 合并后的三个 Sample + 51 题对应 summary + 合并后的 Final |

已从原服务器独立 API 账本取回 79 条旧 summary 请求，其中 51 条成功且有 usage；全量核对为 Input 780,184、Output 513,592、缓存 input 75,904，与此前审计总数一致。按以下证据完成逐题唯一对应：

1. 用完成元数据与账本完成时间筛选候选，并从唯一匹配建立账号与账本 key 指纹的对应关系。
2. 加入同一账号约束和一题一请求约束，唯一确定 49 题。
3. 剩余两题完成时间相距约 0.01 秒，不能仅按最近时间猜测。它们的启动事件相隔约 39 秒，与各自 API 请求的开始时间对应，消除歧义。
4. 核对全部 51 条成功请求一一对应，只保留未被新 Final 替换的 28 题，共 **547,299 tokens**。另外 23 题旧 summary 被排除，不按 28/51 比例分摊。

| 完整 TTS 的 51 题合计 | 实际 tokens |
| --- | ---: |
| Input（含缓存） | 1,124,818,091 |
| 缓存 input | 1,083,367,051 |
| 非缓存 input | 41,451,040 |
| Output（含 thinking） | 9,107,840 |
| **总 tokens** | **1,133,925,931** |

平均每题 **22,233,841.78 tokens**。README 中 TTS Final 已包括 Sample 1/2/3，不能把四行再相加。上述为 provider usage 实际记录值，没有 Token 估算区间。

## 美元费用与缓存计费

采用[阿里云百炼北京地域 Kimi 公开价格](https://help.aliyun.com/zh/model-studio/kimi-k2-7-code)，核对日期 2026-11-09；沿用固定汇率 **1 USD = 6.7168 CNY**。这是公开原价估算，不含账户折扣，不是提供商账单。

| 计费类别 | CNY / 百万 tokens | USD / 百万 tokens |
| --- | ---: | ---: |
| 普通非缓存输入 | 6.5 | 0.967723 |
| 隐式缓存命中 | 1.3 | 0.193545 |
| 显式缓存命中 | 0.65 | 0.096772 |
| 显式缓存创建 | 8.125 | 1.209653 |
| 输出（含 thinking） | 27 | 4.019771 |

显式缓存创建计入“非缓存 input”及其费用；缓存创建 token 没有额外加到 Input 或总 tokens 中。已记录缓存类型与创建量的原始请求使用其实际分类。两类缓存的区别见[官方缓存说明](https://help.aliyun.com/zh/model-studio/context-cache)。

```text
已知非缓存输入费用 = (普通输入 × 6.5 + 显式缓存创建 × 8.125) / 1,000,000 / 6.7168
已知缓存输入费用 = (隐式缓存命中 × 1.3 + 显式缓存命中 × 0.65) / 1,000,000 / 6.7168
输出费用 = output × 27 / 1,000,000 / 6.7168
```

补跑账本的 `normalize_usage` 仅保存 input/output/cache 等数量，没有保存 `cache_type`、`cache_creation_input_tokens`。配置中出现 `cache_control` 不能替代提供商的实际计费记录，因此对这部分用量给出保守费用区间：缓存命中按 0.65–1.3 CNY/M，非缓存输入按 6.5–8.125 CNY/M。后者上界允许全部非缓存输入被按显式创建计费。Output 的单价确定。

| 所选生成阶段 | 已知普通输入 | 已知显式创建 | 已知显式命中 | 已知隐式命中 | 未保存类型的非缓存输入 | 未保存类型的缓存命中 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| sample-1 | 5,468,699 | 70,511 | 70,511 | 123,316,224 | 4,342,927 | 145,555,713 |
| sample-2 | 6,146,547 | 59,685 | 90,495 | 132,858,496 | 4,172,154 | 143,653,307 |
| sample-3 | 5,846,751 | 42,983 | 106,815 | 125,589,760 | 4,133,113 | 152,374,894 |
| final | 5,056,400 | 76,862 | 76,862 | 90,068,096 | 5,282,574 | 169,579,126 |

README 的费用上下界分别把已知分类费用、未保存分类部分的价格边界相加；旧 summary 已精确归属，不再引入 Token 不确定性。**完整 TTS 已记录用量的公开价估算为 $227.29–$290.77，平均每题 $4.4566–$5.7014**。费用范围只来自补跑缓存计费类别未保存，与 Token 数量无关。它不是统计置信区间，也不是完整账单的上下界；未返回 usage 的请求可能另有费用。

## 数据完整性与异常结束

旧服务器数据已经备份。传输记录为 13,956 个文件、7,109,140,049 字节，完成状态为 completed，校验错误为 0。原始 Sample 1/2/3 和 Final 各有 51 题轨迹、生成日志和 API 日志，retry8 有 8 题。对应评测用于本次逐题哈希核对。

旧备份中的 `summary_result.json` 没有 token usage，因此首次统计只能给出 summary 归属范围。现已重新登录原服务器，以只读方式读取独立 SQLite API 账本，保存 summary 请求记录，并结合原备份中的启动事件和完成元数据补齐逐题用量。全量与此前发布的[账本审计结果](../results/kimi_hard51_usage_audit.json)一致；本文与 README 已改为实际 Token 数值。

| 本轮保留的补跑结果 | 数量 | 正常 submitted | 其他生成结束状态 |
| --- | ---: | ---: | --- |
| Sample 1 | 18 | 17 | 1 条 exit_format |
| Sample 2 | 17 | 16 | 1 条 exit_format |
| Sample 3 | 16 | 15 | 1 条 exit_step_limit |
| Final | 23 | 20 | 3 条 exit_total_execution_time |

这 74 条均有补丁和对应评测；其中 6 条不能称作正常完成生成，但也不能从分母中删除。`exit_total_execution_time` 是累计工具执行预算耗尽，与 7,200 秒任务墙钟预算不同。异常生成退出与评测是否通过分别记录。

保留的补跑生成请求中，缺失 usage 的数量分别为 58、159、156、370；23 题新 summary 另有 6 次无 usage 的请求。这合计 **749 次仅覆盖本轮保留的补跑及新 summary**，不包含保留原始运行中无法逐题归属的缺失用量请求，不能称为完整 TTS 的缺失请求总数。所有缺失 usage 都视为未知，不视为免费。

此次混合了原始设置、上下文补跑和 2h 补跑，属于合并结果，不能称为统一预算的独立 pass@1。评测、服务器和无关试验的成本不在本表内。

## 复核与重算

[重算脚本](../scripts/recount_kimi_merged_usage.py)读取私有逐题选择清单、合并审计、原始 API 日志、补跑账本和新取回的旧 summary 账本，校验补丁、评测与 summary 逐题归属后只输出聚合 JSON/CSV。公开文件不包含 API key、账号、题目 ID、提示词、轨迹、补丁或服务器路径。

```bash
python3 scripts/recount_kimi_merged_usage.py \
  --selected-results /private/audit/selected-results.json \
  --merge-audit /private/audit/audit.json \
  --original-audit results/kimi_hard51_usage_audit.json \
  --original-root /private/backup/TSE-Revision \
  --context-run /private/context-rerun \
  --retry-run /private/final-2h-rerun \
  --summary-ledger /private/backup/kimi-summary-ledger.json \
  --output results/kimi_hard51_merged_usage_20261109.json
```

输出记录三个输入审计文件、新取回的 summary 账本以及启动事件日志的 SHA-256，以固定本次选择与统计证据。原始逐题输入保存在私有实验数据中，公共仓库的聚合文件不能单独重建实验。
