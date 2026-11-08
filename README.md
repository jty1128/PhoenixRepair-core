# PhoenixRepair Core

当前分支 **`reproduce/glm-5.2`** 默认使用 **GLM-5.2**。TTS Final 33/51；P0 32/51。恢复策略及默认参数见 [恢复说明](docs/recovery.md)，历史成绩不是本次重新评分。

## 实验结果与每题用量

数据集：SWE-bench Pro v2 HARD-51，每组 51 题。分数沿用历史评测；用量于 **2026-11-08 根据原服务器 API 账本、本机账本及原始响应重新统计**。本次没有重新运行模型或评测。

**输入包含缓存命中部分；缓存不是额外 Token。合计＝输入＋输出。** 下表均为每题平均值：M＝百万 Token，K＝千 Token。费用按百炼北京地域公开按量价格估算并折算美元，未扣除优惠、免费额度或资源包。

### GLM-5.2

| 实验 | 分数 | 输入 / task | 其中缓存 / task | 输出 / task | 合计 / task | 估算 USD / task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | 33/51（64.71%） | 6.716M | 6.378M | 37.302K | 6.754M | $2.4579 |
| Sample 2 | 31/51（60.78%） | 6.437M | 6.174M | 34.179K | 6.471M | $2.2943 |
| Sample 3 | 30/51（58.82%） | 6.176M | 5.897M | 31.321K | 6.207M | $2.2183 |
| TTS Final | 33/51（64.71%） | 24.240M | 23.105M | 128.072K | 24.368M | $8.7657 |
| **我们的方法 · P0** | 32/51（62.75%） | 4.167M | 3.921M | 35.953K | 4.203M | $1.6105 |

### Qwen-3.7 Plus

| 实验 | 分数 | 输入 / task | 其中缓存 / task | 输出 / task | 合计 / task | 估算 USD / task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | 13/51（25.49%） | 3.929M | 3.668M | 24.412K | 3.953M | $0.3251 |
| Sample 2 | 16/51（31.37%） | 3.742M | 3.472M | 23.109K | 3.765M | $0.3146 |
| Sample 3 | 12/51（23.53%） | 4.040M | 3.773M | 25.431K | 4.066M | $0.3345 |
| TTS Final | 17/51（33.33%） | 15.053M | 13.999M | 97.435K | 15.150M | $1.2635 |
| **我们的方法 · P0** | 21/51（41.18%） | 3.946M | 3.531M | 32.373K | 3.978M | $0.3724 |

**本仓库的 `bash our_method.sh` 对应上表的审查前 P0。** 费用已包含复用定位的原始消耗，以及候选生成、必要的 summary 和首次提交前的 final author；不包含 P1 审查、修复及复查。P0 的生成指导仍属于提交前流程。

### Kimi K2.7 Code（Thinking）

| 实验 | 分数 | 输入 / task | 其中缓存 / task | 输出 / task | 合计 / task | 估算 USD / task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | 26/51（50.98%） | 5.837M | 5.503M | 43.808K | 5.881M | $1.5648 |
| Sample 2 | 36/51（70.59%） | 5.429M | 5.092M | 45.592K | 5.474M | $1.4947 |
| Sample 3 | 32/51（62.75%） | 5.473M | 5.151M | 42.895K | 5.516M | $1.4816 |
| TTS Final | 34/51（66.67%） | 22.752M | 21.365M | 192.934K | 22.945M | $6.2538 |

**TTS Final 采用 2026-11-08 补跑后的结果：34/51（66.67%）。** 对原始 final 中 8 道生成异常且未通过的题，复用已经校验的原始 `summary.txt`，从 base commit 重新生成并评测；本轮新增通过 2 题。补跑每题生成上限为 7,200 秒，原始轨迹沿用 3,000 秒；最多 200 步，模型 thinking 与 reasoning 回传保持开启。生成并发由 8 调整为 4（已有任务自然结束）。这是包含追加预算的结果，不能当作统一单次预算的 pass@1。

**TTS 用量包含 Sample 1＋Sample 2＋Sample 3＋51 次成功 summary＋原始 51 题 final＋本轮全部 8 题补跑**，没有扣除被替换的原始失败尝试，也没有重复计算复用 summary。全流程已知用量为 **1,170,188,294 tokens**，其中输入 **1,160,348,684**（含缓存命中 **1,089,614,156**）、输出 **9,839,610**；公开价估算 **¥2,142.29（$318.94）**。本轮 8 题补跑单独增加 **55,677,505 tokens / ¥107.71**。其中 424 次响应记录显式缓存，创建 457,066 tokens、命中 610,380 tokens；创建量和命中量都已包含在输入中，未额外累加为 Token 消耗。费用按显式创建、显式命中及其余隐式缓存分项计算。这些是对应保留实验的用量，不是整台服务器的所有历史消耗。

所有 **19,304 条有 usage 的响应**均完成账本匹配；另有 **2,634 条请求未返回 usage**，包括超时、限流、鉴权错误、其他错误及 pending，未知消耗不当作零。输出采用 API `completion_tokens`，已包含返回的 reasoning 消耗，不再额外相加。表格各行共享 sample 阶段，不能跨行相加。

分项、请求覆盖率和来源指纹：[Kimi CSV](results/kimi_hard51_usage_audit.csv) · [Kimi JSON](results/kimi_hard51_usage_audit.json)。原始 final 与补跑的分项在 JSON 中保留。未计入另一个独立的 `final-sample-2h-20261107` 实验，也未计入废弃 worker、服务器和评测算力费用。

### 计费依据

价格核对日期为 2026-11-08，单位为人民币元 / 百万 Token：

| 模型 | 非缓存输入 | 缓存命中输入 | 输出 | 适用范围 |
| --- | ---: | ---: | ---: | --- |
| GLM-5.2 | ¥8 | ¥2 | ¥28 | 北京地域，不分输入阶梯 |
| Qwen-3.7 Plus | ¥2 | ¥0.4 | ¥8 | 北京地域，单次输入 ≤256K |
| Kimi K2.7 Code | ¥6.5 | 隐式 ¥1.3 / 显式 ¥0.65 | ¥27 | 北京地域；显式缓存创建 ¥8.125 |

来源：[百炼 GLM-5.2 定价](https://help.aliyun.com/zh/model-studio/glm-5-2)、[百炼 Qwen-3.7 Plus 定价](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)。Kimi 来源：[百炼 Kimi K2.7 Code 定价](https://help.aliyun.com/zh/model-studio/kimi-k2-7-code)。Qwen 的每条计费请求均检查为 ≤256K；GLM/Qwen 沿用原审计中的隐式缓存估算。Kimi 根据原始响应中的 `cache_type=ephemeral`、`cache_creation_input_tokens` 区分显式缓存，其余仅返回 `cached_tokens` 的响应按隐式缓存估算；summary 不带缓存标记，按隐式缓存计价。[显式/隐式缓存计费规则](https://help.aliyun.com/zh/model-studio/context-cache)。所有模型均不套用 Batch 价格。

美元使用固定参考汇率 **1 USD＝6.7168 CNY**，来自 [中国外汇交易中心 2026-11-06 人民币汇率中间价](https://www.chinamoney.com.cn/chinese/bkccpr/)。这是人民币费用的统一换算，不是国际站美元定价，也不是各历史交易日的实际结算汇率。

```text
I = 输入 Token（包含缓存）
C = 缓存命中输入 Token
O = 输出 Token（API completion_tokens，包含工具调用参数）
普通隐式缓存人民币费用 = ((I - C) × 非缓存输入价 + C × 缓存价 + O × 输出价) / 1,000,000
Kimi 混合缓存：W=显式创建，Ce=显式命中，Ci=隐式命中，C=Ce+Ci
Kimi 人民币费用 = ((I - C - W) × 6.5 + W × 8.125 + Ce × 0.65 + Ci × 1.3 + O × 27) / 1,000,000
每题美元费用 = 人民币费用 / 6.7168 / 51
```

### 统计范围与完整性

- **Sample 1/2/3**：各自对应的 51 条保留轨迹，包含这些进程内有 usage 的重试；Qwen Sample 1 复用的 6 题计入原始生成用量。
- **TTS Final**：三个 sample＋summary＋final 全流程；GLM/Qwen 各 51 个 summary 已从原账本补齐，不再使用旧表的 `≥`。Kimi 还包含上述 8 题追加补跑，51 个 summary 的 79 次请求中有 51 次成功，其余 28 次没有 usage。
- **P0**：两个模型各 51 题均匹配；通过冻结检查点找回 GLM 6 个、Qwen 5 个恢复前的生成进程。按首次提交的调用边界截断，先截断请求序列再处理错误请求，避免把后续修复请求移入 P0。定位响应与保存的用量汇总逐题一致。
- **旧计数已更正**：旧 `model_stats` 使用框架计数，可能漏掉工具调用输出及重试，与 API usage 口径不同。JSON/CSV 保留旧值供追溯；正文表格采用 API 返回值，不能混用两个版本。
- **费用是公开价估算，不是实付账单**：所有保留任务及成功响应均已匹配。错误、超时或仍标记 pending 的请求若没有返回 usage，单独保留计数，不臆造 Token 或断言收费为零。未返回 usage 的请求不纳入上述费用，详见下表和 JSON。
- 表中不包含废弃实验、历史被替换的整轮重跑、Docker/服务器费用；Kimi 明确包含上述 8 题补跑及其原始 final 尝试。TTS 和 P0 包含复用阶段的成本，跨行相加会重复计算共享阶段。

| 实验 | GLM：有 usage 的请求 / 已匹配请求 | Qwen：有 usage 的请求 / 已匹配请求 | Kimi：有 usage 的请求 / 已匹配请求 |
| --- | ---: | ---: | ---: |
| Sample 1 | 6,027 / 6,029 | 4,617 / 4,766 | 4,943 / 5,153 |
| Sample 2 | 6,074 / 6,076 | 4,479 / 4,481 | 4,649 / 5,365 |
| Sample 3 | 5,861 / 5,865 | 4,710 / 4,712 | 4,543 / 5,267 |
| TTS Final | 22,916 / 22,925 | 17,981 / 18,134 | 19,304 / 21,938 |
| P0 | 5,816 / 5,821 | 5,361 / 5,363 | — |

完整精度数据：[CSV](results/hard51_usage_audit.csv) · [JSON（分项、覆盖率、价格及来源指纹）](results/hard51_usage_audit.json)。公开文件仅含聚合数字，不包含账本原件、账户标识、任务轨迹、补丁、提示词或凭据。

重算脚本：[scripts/recount_hard51_usage.py](scripts/recount_hard51_usage.py)。它需要私有实验文件及两个账本，执行只读 SQLite 快照，并在进程匹配不唯一、成功响应缺少 usage、重复计数或计价阶梯不符时停止：

```bash
python scripts/recount_hard51_usage.py \
  --workspace /path/to/private-artifacts \
  --remote-ledger /path/to/remote-ledger-snapshot.sqlite3 \
  --remote-metadata /path/to/remote-run-metadata.json \
  --output results/hard51_usage_audit.json
```

Kimi 重算脚本：[scripts/recount_kimi_hard51_usage.py](scripts/recount_kimi_hard51_usage.py)。读取私有运行目录和只读账本快照，结合请求起止时间、输入/输出/缓存计数及唯一响应确认 worker，再核对全部保留响应；不上传任务或账户信息。

```bash
python scripts/recount_kimi_hard51_usage.py \
  --root /path/to/private/TSE-Revision \
  --ledger /path/to/usage.sqlite3 \
  --output results/kimi_hard51_usage_audit.json
```

## 方法入口

包含三种方法的核心代码、配置、运行说明与上述汇总结果。基于 SWE-agent / SWE-ReX；不包含实验轨迹、历史补丁、数据集、逐题评测日志、API 密钥或机器专用配置。

| 入口 | 方法 |
| --- | --- |
| `bash run_single.sh` | 每题独立生成一个 sample；不同输出目录可分别运行 sample-1/2/3 |
| `bash run_tts.sh` | 默认三个独立 sample → 同模型 summary → 从原始代码重新生成 final patch |
| `bash our_method.sh` | 完整策略定位 → 按方案生成补丁；单候选直接输出，多候选分别生成后 summary → final；最终输出审查前 **P0** |

`bash run_our_method.sh` 是 `our_method.sh` 的兼容入口。

## 安装

要求 Linux x86_64、Python 3.11+、Git，以及当前账号可访问的 Docker daemon。
请克隆本仓库后使用下面的 editable 安装方式；入口会读取源码树中的工具和配置。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,runtime]'
cp .env.example .env
```

在 `.env` 中设置 `PHOENIX_API_KEY`、`INSTANCES`、`PHOENIX_RUNTIME`。如有独立 Docker daemon，可设置 `DOCKER_HOST`。密钥只从环境变量读取，不通过命令行传递或写入运行配置。

任务镜像需事先拉取，且含有对应仓库的原始 base checkout。方法不自动下载或注入官方评测材料。

## 准备工具运行时

已有兼容的独立 Python / SWE-ReX 运行时，可直接配置 `PHOENIX_RUNTIME`。首次安装可执行：

```bash
python -m phoenix.runtime prepare --output "$PWD/.runtime"
python -m phoenix.runtime check --output "$PWD/.runtime"
```

准备过程下载独立 Python 3.11 和工具依赖，因此需要联网。生成时只把工具运行时挂入容器，不替换任务仓库自己的 Python/Node/Go 环境。`.runtime` 不上传 Git。

## 任务清单

`INSTANCES` 指向用户自行准备的 JSON 数组或 JSONL。每题只使用以下五个字段；其他字段不会传给模型：

```json
[
  {
    "instance_id": "your-task-id",
    "problem_statement": "Original issue description and requirements",
    "base_commit": "the-original-commit-sha",
    "repo_name": "app",
    "image_name": "your-prepared-task-image:tag"
  }
]
```

镜像中仓库路径为 `/<repo_name>`。生成前会在容器内隔离 Git 历史；定位只读取经 base checkout 校验导出的当前源码，不运行测试或生成 patch。

## 运行

各入口默认读取 `configs/single.yaml`、`configs/tts.yaml`、`configs/our_method.yaml`。可直接修改配置，也可用命令行覆盖。所有示例复用 `.env` 中的任务、密钥和运行时配置。

```bash
# 不调用模型、不启动容器，检查清单和配置
bash run_single.sh --dry-run
bash run_tts.sh --dry-run
bash our_method.sh --dry-run

# 独立 samples
bash run_single.sh --model glm-5.2 --output runs/glm-sample-1
bash run_single.sh --model glm-5.2 --output runs/glm-sample-2

# TTS，包括三个 samples、summary、final
bash run_tts.sh --model qwen3.7-plus --samples 3 --output runs/qwen-tts

# 我们的方法：完整定位后生成审查前 P0
bash our_method.sh --model qwen3.7-plus --output runs/qwen-p0

# 也可以分开执行两个阶段
bash our_method.sh --stage localization --output runs/qwen-location --model qwen3.7-plus
bash our_method.sh --stage patch --locations runs/qwen-location/localization \
  --model qwen3.7-plus --output runs/qwen-p0-from-locations
```

可用 `--limit 6` 小批量验证；`--instance-id ID` 可重复指定；`--concurrency 4` 控制同时处理的题目数量。所有阶段在同一题内有序执行。`--help` 查看完整参数。

新实验使用新输出目录。中断后使用原命令加 `--resume`：核对原输入、模型、方法和预算，接管本次运行的 worker，复用哈希校验通过的已完成阶段。已经耗尽恢复次数的阶段仍返回失败，不会通过重启获得新预算。输出 patch 不代表通过官方评测，本仓库不自动评测。

```bash
bash run_tts.sh --model qwen3.7-plus --output runs/qwen-tts --resume
bash our_method.sh --model qwen3.7-plus --output runs/qwen-p0 --resume

# 可选：按实验磁盘的持续 I/O 压力调整准入；设备名需替换成本机实际设备。
bash our_method.sh --model glm-5.2 --output runs/glm-p0 \
  --concurrency 12 --adaptive-io --io-device YOUR_BLOCK_DEVICE
```

## 模型分支与恢复

共享实现维护在 `main`；以下 Git 分支分别设置模型默认值，保留同一套方法代码和恢复测试：

| 分支 | 默认模型 | 对应方法与历史通过数 |
| --- | --- | --- |
| [`reproduce/glm-5.2`](https://github.com/jty1128/PhoenixRepair-core/tree/reproduce/glm-5.2) | GLM-5.2 | TTS Final 33/51；P0 32/51 |
| [`reproduce/qwen3.7-plus`](https://github.com/jty1128/PhoenixRepair-core/tree/reproduce/qwen3.7-plus) | Qwen-3.7 Plus | TTS Final 17/51；P0 21/51 |
| [`reproduce/kimi-k2.7-code`](https://github.com/jty1128/PhoenixRepair-core/tree/reproduce/kimi-k2.7-code) | Kimi K2.7 Code | 支持 single/TTS；没有本表对应的 P0 设定或成绩 |

Kimi 新记录的 34/51 包含 8 题追加的 7,200 秒 final 补跑；该复合成绩不等于此分支默认的单轮 3,000 秒 TTS。追加实验需要单独声明预算与全部尝试成本，不能通过 `--resume` 重置或增加原预算。

`phoenix/recovery_profiles.py` 明确记录模型差异。GLM/Qwen 的 Single/TTS 在 7,140 秒停止生成，预留 60 秒提取当前补丁；Kimi 保留完整 3,000 秒生成时限，之后另用最多 60 秒提取。Kimi 保留 thinking/reasoning 回传。各模型的请求超时、输出上限和 summary 重试规则见下表及后文，不会在恢复时替换模型。

已移植镜像检查与固定 digest、环境启动重试、运行时兼容修复、命令/启动 I/O 闸门、可选自适应准入、超时补丁提取、轨迹续跑、跨容器提交重放和测试文件清理。提交重放只验证原补丁的文件内容与权限；没有新增模型审核、P1 或按题目定制的修复逻辑。

**TTS 与 P0 的预算单位不同，但都不因恢复重置。** TTS 的每个 sample/final 各自有原来的 200 步，summary 另有有界次数；P0 的候选、summary 和 final 共用原 author 配额，单候选仍最多 160 次。比如 P0 已用 100 次，恢复后只剩 60 次，而不是再给 160 次。已经发出但结果未知的调用仍计入消耗；重放/校验本身不调用模型。[完整恢复规则和验证方式](docs/recovery.md)。

## P0 的准确含义

这里的 `our_method.sh` 对应“审查前 P0”，不是旧的无生成指导版本，也不是审核后的 P1：

- 使用完整策略定位流程；每个候选应形成完整方案，并与原始需求对应。
- 生成时把定位视为待验证的建议，以原始 issue 为准；检查调用方、关联行为、边界情况和实际 diff。
- 保留当时 P0 生成前的 author prompt 和预算提醒。原提示中的审核安排保留以匹配生成条件，但本入口在首次提交后结束，**不调用审核模型，不修订，不选择 P1**。
- 单候选的原始 200-call 配额中，P0 author 最多使用 160 次，原预留的 40 次不会被执行。多候选时，候选共享前半预算，summary 使用一次调用，final 可使用上游节省的额度，同时保留原 40 次预留。定位单独使用历史设定的 150 步；`--localization-max-steps` 可显式调整，最多 200 步，调整后属于不同的实验设定。
- 提交阶段只执行确定性的补丁校验；如仅混入测试文件修改，保留原稿并排除测试 hunks，不改变生产代码 hunks，不产生额外模型请求。

也可通过 `--locations` 复用自己已有的同模型定位。目录必须含 `instances.json`，以及 `<model>/<instance_id>/location_candidates.json` 和 `report.json`；会检查题目、base commit、模型及候选完整性。仓库不携带历史定位数据，因此重新生成不保证复现某次历史运行的具体分数。

## 模型设定

定位和 patch 生成使用各自的历史配置，不能把 patch 参数直接套到定位阶段。完整策略定位在 GLM/Qwen 上均为：thinking 关闭、temperature=0、top_p=1、输出上限 32,768、请求超时 600 秒、tool_choice=auto（到检查点时强制 checkpoint）、最多 150 步。上下文达到 100,000 后压至目标 90,000，压缩后仍超过 110,000 则停止；保留首尾各 5 轮工具输出。连续 10 次相同动作会停止，HTTP 传输最多尝试 5 次。历史定位无独立总时限，公开入口可通过 `--localization-timeout` 显式增加。

| Patch 生成参数 | GLM-5.2 | Qwen-3.7 Plus | Kimi K2.7 Code |
| --- | --- | --- | --- |
| 模型 ID | `glm-5.2` | `qwen3.7-plus` | `kimi-k2.7-code` |
| Thinking | 关闭 | 关闭 | 开启并回传 reasoning |
| temperature / top_p | 0 / 1 | 0 / 1 | 0 / 1 |
| max_tokens | 8,192 | 32,768 | 32,768 |
| 单次请求超时 | 240 秒 | 600 秒 | 240 秒 |
| tool_choice | required | auto | auto |
| 每条轨迹最多步数 | 200 | 200 | 200 |
| 单条生成时限 | 7,200 秒 | 7,200 秒 | 3,000 秒 |

输入上限为 131,072；上下文达到 120,000 时压缩至目标 110,000，保留首尾各 5 轮。API 内层 `num_retries=0`；single/TTS 沿用框架最多 20 次尝试，P0 author 每次 query 只尝试一次。不要把两层重试设置混为一谈。

TTS summary 使用同一个模型，temperature=0.7、top_p=1。GLM/Qwen 输出上限为 8,192、请求超时为 120 秒；每轮最多 5 次传输尝试，格式不合格或传输耗尽后最多进行 3 轮，总上限为 15 次请求。GLM/Qwen 沿用历史格式校验：接受普通或加粗标题，以及带方括号的样本序号；必须包含 `BEST_SAMPLE`、`REASONING:`、`INSIGHTS FOR NEXT ATTEMPT:`，且总结至少 100 字符。Kimi 输出上限为 32,768、超时为 240 秒；每轮最多 3 次传输尝试，耗尽即停止；格式不合格最多 3 轮，并拒绝截断的 summary。

P0 多候选 summary 使用历史 P0 专用设置：同模型、temperature=0、top_p=1；GLM 输出上限 8,192、读取超时 240 秒，Qwen 为 32,768、600 秒，连接超时均为 30 秒。受共享预算限制只调用一次，读取完整候选 diff，并追加“对照原始 issue、保留不确定性、避免合并不兼容方案”的原始提示。已接入与 author 共用的账户/模型请求间隔控制，最短间隔 2 秒。历史流程将非空 summary 交给 final，不使用 TTS 的额外格式门槛；单候选不经过 summary。

GLM/Qwen 支持三个入口；Kimi 支持 single/TTS。模型支持情况取决于你的服务商账号，不会用另一模型自动替代。

## 输出

- `plan.json`：无密钥的运行配置与输入指纹。
- `<model>/<instance_id>/report.json`：生成状态、步骤与调用数。
- `<model>/<instance_id>/final.patch`：最终生成补丁；我们的方法另存同内容的 `P0.patch`。
- 各阶段目录保留配置、日志、轨迹、候选与 summary，便于审计。
- `attempts/`、`execution.json`：原始尝试、恢复原因、累计预算及执行时限；`result.json` 保存已完成补丁哈希。恢复保留原稿，不覆盖为一次新生成。
- `api-usage.sqlite3`：框架取得的原始 API 用量账本。旧 `model_stats.tokens_received` 可能不含工具调用参数，不能作为完整计费用量。

运行输出、数据和密钥全部由 `.gitignore` 排除。维护时只提交代码、配置模板和合成单元测试。

## 验证与来源

```bash
python -m pytest -q
```

默认测试不访问模型 API。可另设 `PHOENIX_SMOKE_BASE_IMAGE`（本地已有、含 Git/Bash 的镜像）和 `PHOENIX_SMOKE_RUNTIME` 后运行 `python -m pytest tests/test_docker.py -q`，分别验证 GLM/Qwen 配置下的源码导出、完整定位、真实容器编辑、历史隔离和 P0 提交；该测试使用合成代码与固定动作回放，不调用模型。

P0 历史方法核对覆盖 102 份冻结定位、115 个生成阶段的指导文本与 agent 配置，以及 6 个多候选 summary 请求。规范化工具安装路径与密钥环境变量名后，模型参数、提示词、115 个阶段的生成调用配额均一致；交互命令拦截列表仍有路径差异，不能称所有 agent 配置字段完全相同。6 个 summary 请求可从原候选逐字段重建，1,410 种调用余额/阶段组合的预算提醒文本也一致。历史评分的 102 份输入补丁与对应 P0 文件一致，复核通过数为 GLM 32、Qwen 21。

TTS 历史方法核对覆盖 GLM/Qwen 共 408 个生成阶段（三个 sample 与 final，各 51 题）、102 份 summary 提示与请求参数，以及 306 份冻结 sample 补丁的哈希。实际渲染的 sample/final 提示词和 summary 请求均一致；102 份历史 summary 均通过对齐后的格式校验。离线流程测试覆盖三个 sample 独立生成、同模型总结、全新 final 输出和有界重试。

TTS 的运行实现仍有明确差异：公开版保留了提交工具的超时保护（`submit` 从普通命令的 30 秒改为 600 秒）、有时限的原子补丁收集和 binary diff 输出，以及交互命令拦截列表的路径修正。配置还显式加入了三个关闭状态的字段：完整策略定位、thinking 保留和请求间隔控制，均不在 GLM/Qwen TTS 中启用。因此这里对齐的是核心方法、提示词和模型参数，并非历史服务器环境的逐字复制；这些检查没有调用付费模型或重新评分，也不保证重新生成能精确复现表中分数。

2026-11-08 的进一步核对基于公开提交 `13f54e7`（方法代码与 `8f35b56` 相同），直接从 Git 提取已上传文件运行测试，没有混入本地未提交代码。定位的 15 个依赖文件与生成冻结定位时的快照逐字一致；历史定位快照的 262 个文件和 P0 快照的 307 个文件也均通过各自保存的哈希清单校验。

此次恢复移植后，完整实验仍有以下复现边界：

- **定位输入**：表中 P0 使用固定的 102 份历史定位，`our_method.sh` 默认重新定位。对应原输入的运行需要通过 `--locations` 提供同模型冻结定位；这些私有产物没有上传。
- **异常路径的明确改进**：恢复控制器已移植并增加持久化日志，跨进程恢复不会重置已用预算。Kimi 的超时提取改为先禁止新操作并停止容器命令，再读取 diff，避免边改边提取；这不是历史启动器的逐字复制。P0 仍停在首次有效提交，不执行历史控制器中后续的监督/P1 阶段。
- **运行环境**：运行开始会固定用户提供镜像的实际 digest；支持 `pin_cpus` 和通过环境变量 `PHOENIX_CONTAINER_PROXY` 配置容器代理。严格复现仍需用户提供相同镜像、依赖和网络条件，仓库不携带私有服务器配置。
- **分数验证**：已重新核对 GLM/Qwen 的 408 个 TTS 生成阶段、102 份 TTS summary、115 个 P0 阶段、6 份 P0 summary，以及 102 份原定位/评分补丁。正常路径的提示词、模型参数和调用配额对应；故障恢复通过离线注入和真实 Docker 回放验证。这些检查没有重新请求付费模型或进行全量评分，不能据此保证新生成的数值完全等于历史成绩。

分数来源按题目、模型及补丁 SHA256 关联：Single/TTS 八组各 51 题均能关联到历史评测，计数与表中一致；其中每组同一题的主评测记录不在本地备份中，使用了补丁哈希一致的旧基础设施尝试归档。因此这些分数是可追溯的历史记录，并非本次重新完成的完整评测。P0 的 102 份评分输入则全部匹配对应的 P0 评测记录。

核心 agent 来自 SWE-agent 1.1.0 的项目内改版；保留原 MIT LICENSE。TTS 提示词与原始实验的 sample/summary/final 逻辑一致。完整策略定位和 P0 author guidance 从对应方法快照提取；不包含按任务 ID 分支的修复规则、参考补丁或评测答案。公开版本将服务器专用路径和控制器替换为可配置入口。
