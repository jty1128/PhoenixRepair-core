# PhoenixRepair Core

## Overall（截止2026-11-9）

以下为 **SWE-bench Pro v2 HARD-51** 的成绩汇总，每组 51 题

| 方法 | GLM-5.2 | Qwen-3.7 Plus | Kimi K2.7 Code |
| --- | ---: | ---: | ---: |
| Sample 1 | 31/51（60.78%） | 13/51（25.49%） | 26/51（50.98%） |
| Sample 2 | 33/51（64.71%） | 16/51（31.37%） | 36/51（70.59%） |
| Sample 3 | 31/51（60.78%） | 12/51（23.53%） | 32/51（62.75%） |
| TTS Final | 33/51（64.71%） | 17/51（33.33%） | 34/51（66.67%） |
| **Our Method · P0** | **35/51（68.63%）** | **21/51（41.18%）** | 未运行 |

### tokens计费

下表同时列出 **GLM 和 Qwen 的 Sample 1、Sample 2、Sample 3、TTS Final、Our Method · P0**。每 task 均为对应实验 51 题的平均值，包含未通过评测的题；。**Input 已包含缓存命中；总 tokens = input + output tokens。**

- **Sample 1/2/3**：各自生成一个样本的成本。
- **TTS Final**：三个 samples＋同模型 summary＋final 生成的完整流程成本，不是只计算最后一段生成。
- **Our Method · P0**：定位＋候选生成＋必要的 summary＋首次 P0 提交前的 final 生成，包含复用定位原本的成本。

按阿里云百炼北京地域在线调用公开价格估算， 下列单价及费用统一使用 **美元（USD）**。沿用本次审计的固定汇率 **1 USD = 6.7168 CNY** 将人民币公开价除以 6.7168 换算。Qwen 本表请求均已核对为单次输入 ≤256K，采用隐式缓存价格。

| 模型 | 非缓存 input（USD / 百万 tokens） | 缓存命中 input（USD / 百万 tokens） | Output（USD / 百万 tokens） |
| --- | ---: | ---: | ---: |
| [GLM-5.2](https://help.aliyun.com/zh/model-studio/glm-5-2) | $1.1910 | $0.2978 | $4.1687 |
| [Qwen-3.7 Plus（输入 ≤256K）](https://help.aliyun.com/zh/model-studio/qwen3-7-plus) | $0.2978 | $0.0596 | $1.1910 |

```text
非缓存 input = input - 缓存命中 input
费用（USD）= ((input - cache) × 非缓存输入美元单价 + cache × 缓存美元单价 + output × 输出美元单价) / 1,000,000
```

### GLM-5.2：实验消耗

| 实验 | 成绩 | 总 tokens / task | Input tokens / task（含缓存） | 缓存 input tokens / task | 非缓存 input tokens / task | Output tokens / task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | 31/51 | 6,966,142.20 | 6,933,399.82 | 6,833,091.76 | 100,308.06 | 32,742.37 |
| Sample 2 | 33/51 | 6,542,865.69 | 6,510,525.57 | 6,412,483.76 | 98,041.80 | 32,340.12 |
| Sample 3 | 31/51 | 6,151,591.41 | 6,120,532.86 | 6,029,552.94 | 90,979.92 | 31,058.55 |
| TTS Final | 33/51 | 24,368,411.88 | 24,240,340.20 | 23,105,164.55 | 1,135,175.65 | 128,071.69 |
| Our Method · P0 | 35/51 | 5,208,154.31 | 5,168,489.92 | 4,887,085.18 | 281,404.75 | 39,664.39 |

GLM-5.2 分项计费（美元 USD）：

| 实验 | 非缓存 input 费用 / task | 缓存 input 费用 / task | Output 费用 / task | 合计费用 / task | 51 题总费用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | $0.1195 | $2.0346 | $0.1365 | $2.2906 | $116.82 |
| Sample 2 | $0.1168 | $1.9094 | $0.1348 | $2.1610 | $110.21 |
| Sample 3 | $0.1084 | $1.7954 | $0.1295 | $2.0332 | $103.69 |
| TTS Final | $1.3520 | $6.8798 | $0.5339 | $8.7657 | $447.05 |
| Our Method · P0 | $0.3352 | $1.4552 | $0.1653 | $1.9557 | $99.74 |

### Qwen-3.7 Plus：实验消耗

| 实验 | 成绩 | 总 tokens / task | Input tokens / task（含缓存） | 缓存 input tokens / task | 非缓存 input tokens / task | Output tokens / task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | 13/51 | 3,953,197.78 | 3,928,785.84 | 3,668,174.20 | 260,611.65 | 24,411.94 |
| Sample 2 | 16/51 | 3,764,992.63 | 3,741,883.75 | 3,472,063.96 | 269,819.78 | 23,108.88 |
| Sample 3 | 12/51 | 4,065,643.14 | 4,040,212.37 | 3,773,365.31 | 266,847.06 | 25,430.76 |
| TTS Final | 17/51 | 15,150,081.55 | 15,052,647.00 | 13,998,829.49 | 1,053,817.51 | 97,434.55 |
| Our Method · P0 | 21/51 | 3,978,237.39 | 3,945,864.20 | 3,530,722.96 | 415,141.24 | 32,373.20 |

Qwen-3.7 Plus 分项计费（美元 USD）：

| 实验 | 非缓存 input 费用 / task | 缓存 input 费用 / task | Output 费用 / task | 合计费用 / task | 51 题总费用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | $0.0776 | $0.2184 | $0.0291 | $0.3251 | $16.58 |
| Sample 2 | $0.0803 | $0.2068 | $0.0275 | $0.3146 | $16.05 |
| Sample 3 | $0.0795 | $0.2247 | $0.0303 | $0.3345 | $17.06 |
| TTS Final | $0.3138 | $0.8337 | $0.1160 | $1.2635 | $64.44 |
| Our Method · P0 | $0.1236 | $0.2103 | $0.0386 | $0.3724 | $18.99 |


## GLM-5.2 参数设定（2026-11-09）

### 生成预算与监督阶段的规则

每个任务分配最多 **200 次模型调用**，用于生成首次提交补丁 P0。根据定位候选数量分配候选生成预算，并将未使用的候选额度转给最终生成阶段。**定位采用独立预算，不占这 200 次额度**；可通过 `--locations` 复用已有的同模型定位。

1. **单候选**：author 直接生成 P0，最多使用 200 次调用，不执行 summary 或额外的 final 阶段。
2. **多候选**：候选生成共分配 100 次调用，尽量平均分配；不能整除的余额依次分给前面的候选。例如，两个候选各 50 次，三个候选分别为 34 / 33 / 33 次。
3. **汇总与最终生成**：多候选生成完成后，使用最多 1 次调用生成 summary，再从原始代码开始生成最终 P0。final 获得全部剩余额度，计算方式如下。

```text
final 可用额度 = 200 - 所有候选实际扣减的调用额度之和 - summary 扣减的调用额度
```

正常完成 summary 并进入 final 时，至少保留 99 次额度；候选提前结束所节省的额度全部转入 final。这里是可用上限，各阶段允许提前结束，不要求耗尽额度。

| 定位候选数 | 调用分配 |
| --- | --- |
| 1 | author 最多 200 次，直接生成 P0 |
| 2 | 两个候选各最多 50 次 + summary 最多 1 次 + final 剩余额度（至少 99 次） |
| 3 | 候选最多 34 / 33 / 33 次 + summary 最多 1 次 + final 剩余额度（至少 99 次） |

4. **统一计数**：候选、summary 和 final 共用总预算，合计最多 200 次。失败请求和每次重试均扣减额度，断点恢复不重置预算。轨迹步骤与模型调用额度是两个不同计数。
5. **终止与监督**：完成 P0 提交即结束，不执行独立 reviewer、审查后修复或复审。author 在生成期间自行检查代码、运行测试、修改补丁所需的模型调用，均计入生成预算。

配套运行参数为 **GLM-5.2、单次最大输出 32,768 tokens、任务并发数 4**。并发指同时处理不同任务；同一道题的候选、summary 和 final 按阶段顺序执行。

### 模型与运行参数

| 参数 | GLM-5.2 P0 |
| --- | --- |
| API 模型 / 协议 | `glm-5.2` / DashScope OpenAI-compatible Chat Completions |
| Thinking / temperature / top_p | `false` / `0.0` / `1.0` |
| author 工具选择 | `required` |
| 单次最大输出 | **32,768 tokens**；author 和 P0 summary 一致 |
| 输入上限 | 131,072 tokens |
| 上下文压缩 | 输入超过 120,000 时，目标 110,000；保留首尾各 5 轮 |
| P0 总调用 / 单条轨迹步数上限 | 200 次共享调用 / 200 步 |
| 单个 author 阶段累计时限 | 7,200 秒；恢复不重置；多候选各阶段依次执行 |
| 单次 author API 超时 | 240 秒 |
| author API 重试 | 最多 8 次尝试，包含首次；退避 10–120 秒；SDK `num_retries=0` |
| P0 summary | 最多 1 次请求；temperature=0，top_p=1；连接/读取超时 30/240 秒 |

输入上限为 131,072，输出上限为每次响应 32,768。P0 共享调用上限来自 `max_steps: 200`，输出上限来自 `phoenix/settings.py` 中 GLM 的模型配置；author 和 P0 summary 读取同一个值。本版本保留实验源码的接口，不提供额外的 `p0_call_budget` 或 `max_output_tokens` 配置项。

### 2026-11-08 的代码修复

- **API 超时和连接异常**：P0 对超时、连接中断及 HTTP 408/429/5xx 原地重试，单次 query 最多尝试 8 次；认证、参数、证书错误直接停止该生成。Single/TTS 的 query 上限保留 20 次。
- **耗尽重试的处理**：保存 `api-interrupted.patch`、错误类型与补丁哈希以及完整轨迹，标记 `exit_api`；不会把未完成代码自动提交为成功 P0，也不会开始后续审查。快照包含未跟踪文件，使用独立临时 Git index，保留原暂存区。
- **断点恢复**：可重试 API 故障在仍有调用、步数、时间余额时，最多额外进行一次阶段恢复，载入保存的代码和轨迹。若无法保存快照，保留原容器。`--resume` 沿用原输入、模型、预算和已用计数，不重新开始一份预算。
- **预算计数**：每次内部传输尝试前持久化计数，失败、重试以及已发出但结果未知的请求均占额度。最后一次额度消耗在失败请求上时，仍记录为 API 中断，不能转换为正常预算提交。
- **上下文配置**：输入上限、压缩阈值和压缩目标可显式设置，恢复时检查一致性。调用预算使用 `max_steps`，输出上限使用实验源码中的模型配置。当前 GLM 参数与上表及显式配置一致。

代码位于[模型请求与重试](sweagent/agent/models.py)、[API 错误分类与快照](sweagent/agent/api_errors.py)、[中断状态处理](sweagent/agent/agents.py)、[恢复执行器](phoenix/backend.py)、[持久化状态](phoenix/run_state.py)、[生成与计数](phoenix/worker.py)、[预算调度](phoenix/pipeline.py)及[参数加载](phoenix/settings.py)。测试使用合成错误和补丁，覆盖第八次成功、重试耗尽、最后一次额度失败、恢复后的预算连续性及不进入审查。

### 运行当前版本

```bash
# main 与 reproduce/glm-5.2 均使用当前 GLM P0 默认值。
bash our_method.sh --instances /path/to/instances.json --runtime /path/to/runtime \
  --stage patch --locations /path/to/frozen-locations --output runs/glm-p0-200calls-32k

# 显式选择参数并复用同模型定位；输入和运行时由使用者提供。
bash our_method.sh --config configs/experiments/glm-5.2-p0-200calls-32k.yaml \
  --instances /path/to/instances.json --runtime /path/to/runtime \
  --stage patch --locations /path/to/frozen-locations --dry-run
```

密钥通过本机 `PHOENIX_API_KEY` 或 `.env` 提供。省略 `--stage patch --locations ...` 会重新定位。公开入口生成 P0，不启动官方评测；评测需另行使用对应基准的官方工具。更改参数时使用新输出目录，不能通过 `--resume` 重置预算。

## 方法入口

包含三种方法的核心代码、参数配置、运行说明、合成测试及 README 中的聚合成绩，基于 SWE-agent / SWE-ReX。任务数据、逐题结果、用量统计文件、轨迹、生成补丁、日志、API 密钥和机器专用配置均保留在本机，不纳入提交。

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

### GLM / Qwen 专用启动脚本

以下入口自动选择对应模型的配置，默认均为 4 并发；参数继续传给通用脚本。先按安装说明准备依赖、Docker、任务镜像，并在本机 `.env` 中填写 `PHOENIX_API_KEY`、`INSTANCES`、`PHOENIX_RUNTIME`，API 地址使用 `API_BASE`。

| 方法 | GLM-5.2 | Qwen-3.7 Plus |
| --- | --- | --- |
| 单个 sample | [run_glm_single.sh](run_glm_single.sh) | [run_qwen_single.sh](run_qwen_single.sh) |
| 三个 samples → summary → final | [run_glm_tts.sh](run_glm_tts.sh) | [run_qwen_tts.sh](run_qwen_tts.sh) |
| Our Method：定位 → P0 | [run_glm_our_method.sh](run_glm_our_method.sh) | [run_qwen_our_method.sh](run_qwen_our_method.sh) |

```bash
# GLM-5.2：按需要选择一种方法运行
bash run_glm_single.sh --output runs/glm-single-new
bash run_glm_tts.sh --output runs/glm-tts-new
bash run_glm_our_method.sh --output runs/glm-p0-new

# Qwen-3.7 Plus：按需要选择一种方法运行
bash run_qwen_single.sh --output runs/qwen-single-new
bash run_qwen_tts.sh --output runs/qwen-tts-new
bash run_qwen_our_method.sh --output runs/qwen-p0-new

# 独立生成 Sample 1/2/3 时分别指定新的输出目录
bash run_glm_single.sh --output runs/glm-sample-2-new
bash run_glm_single.sh --output runs/glm-sample-3-new

# GLM P0 复用已保存的同模型定位
bash run_glm_our_method.sh --stage patch \
  --locations /path/to/frozen-locations --output runs/glm-p0-from-locations-new

# 只校验输入和参数，不调用模型或启动容器
bash run_qwen_single.sh --output runs/qwen-single-check --dry-run
```

TTS 入口自行生成三个 samples，随后执行 summary 和 final；上述 Single 输出不会自动作为 TTS 输入。断点恢复使用原命令加 `--resume`，保持原配置及输出目录。

这些入口使用当前仓库快照：GLM P0 为 200 次共享调用、32,768 输出；当前 Qwen P0 配置也为 200 次。README 中 Qwen 的历史 P0 成绩来自 160 次预算的历史实验，当前启动脚本不等同于该历史实验的原样复现。原有 [run_single.sh](run_single.sh)、[run_tts.sh](run_tts.sh)、[our_method.sh](our_method.sh) 及模型配置仍可直接使用，新增入口仅选择配置，未修改实验生成逻辑。

## 模型配置与恢复

代码和模型配置统一维护在 `main`，`reproduce/glm-5.2` 同步当前代码及 GLM 默认配置。各模型配置在下列目录中，使用 `--config` 选择即可；也可用 `--model` 覆盖模型，但其余配置仍来自所选配置文件。

| 配置目录 | 默认模型 | 配置说明 |
| --- | --- | --- |
| [configs/models/glm-5.2](configs/models/glm-5.2) | GLM-5.2 | P0：200 次共享调用、32,768 输出 |
| [configs/models/qwen3.7-plus](configs/models/qwen3.7-plus) | Qwen-3.7 Plus | 支持 single/TTS/P0，使用当前快照的 P0 调度 |
| [configs/models/kimi-k2.7-code](configs/models/kimi-k2.7-code) | Kimi K2.7 Code | 支持 single/TTS；无 P0 配置 |

```bash
bash run_tts.sh --config configs/models/glm-5.2/tts.yaml
bash our_method.sh --config configs/models/qwen3.7-plus/our_method.yaml
bash run_single.sh --config configs/models/kimi-k2.7-code/single.yaml
```

本提交固定为 GLM 200 calls / 32K 实验源码；GLM 的 single/TTS author 也读取该快照中的 32,768 输出配置，TTS summary 的输出上限单独列于下文。其他模型保留切换入口，但当前提交不代表它们各自历史实验的源码版本。Kimi 的 `our_method.yaml` 明确拒绝运行。

`phoenix/recovery_profiles.py` 明确记录模型差异。GLM/Qwen 的 Single/TTS 在 7,140 秒停止生成，预留 60 秒提取当前补丁；Kimi 保留完整 3,000 秒生成时限，之后另用最多 60 秒提取。Kimi 保留 thinking/reasoning 回传。各模型的请求超时、输出上限和 summary 重试规则见下表及后文，不会在恢复时替换模型。

已移植镜像检查与固定 digest、环境启动重试、运行时兼容修复、命令/启动 I/O 闸门、可选自适应准入、超时补丁提取、轨迹续跑、跨容器提交重放和测试文件清理。提交重放只验证原补丁的文件内容与权限；没有新增模型审核、P1 或按题目定制的修复逻辑。

**TTS 与 P0 的预算单位不同，但都不因恢复重置。** TTS 的每个 sample/final 各自最多 200 步，summary 另有有界次数；P0 的候选、summary 和 final 共用 200 次调用。若已用 100 次，最多再用 100 次。未知请求仍计入消耗；重放/校验本身不调用模型。[完整恢复规则和验证方式](docs/recovery.md)。

## P0 的准确含义

这里的 `our_method.sh` 对应“审查前 P0”，不是旧的无生成指导版本，也不是审核后的 P1：

- 使用完整策略定位流程；每个候选应形成完整方案，并与原始需求对应。
- 生成时把定位视为待验证的建议，以原始 issue 为准；检查调用方、关联行为、边界情况和实际 diff。
- 使用实际实验中的完整生成预算提示，已删除原 40 次审查预留说明。本入口在首次提交后结束，**不调用审核模型，不进行审查后的修复，不选择 P1**。
- 单候选最多 200 次；多候选共用前 100 次，summary 一次，final 至少 99 次并继承上游节省的额度。定位单独使用 150 步；`--localization-max-steps` 可显式调整，最多 200 步。
- 提交阶段只执行确定性的补丁校验；如仅混入测试文件修改，保留原稿并排除测试 hunks，不改变生产代码 hunks，不产生额外模型请求。

也可通过 `--locations` 复用自己已有的同模型定位。目录必须含 `instances.json`，以及 `<model>/<instance_id>/location_candidates.json` 和 `report.json`；会检查题目、base commit、模型及候选完整性。定位输入由使用者自行准备，仓库不携带定位数据。

## 模型设定

定位和 patch 生成使用各自的配置，不能把 patch 参数直接套到定位阶段。完整策略定位在 GLM/Qwen 上均为：thinking 关闭、temperature=0、top_p=1、输出上限 32,768、请求超时 600 秒、tool_choice=auto（到检查点时强制 checkpoint）、最多 150 步。上下文达到 100,000 后压至目标 90,000，压缩后仍超过 110,000 则停止；保留首尾各 5 轮工具输出。连续 10 次相同动作会停止，HTTP 传输最多尝试 5 次。历史定位无独立总时限，公开入口可通过 `--localization-timeout` 显式增加。

| Patch 生成参数 | GLM-5.2 | Qwen-3.7 Plus | Kimi K2.7 Code |
| --- | --- | --- | --- |
| 模型 ID | `glm-5.2` | `qwen3.7-plus` | `kimi-k2.7-code` |
| Thinking | 关闭 | 关闭 | 开启并回传 reasoning |
| temperature / top_p | 0 / 1 | 0 / 1 | 0 / 1 |
| max_tokens（当前默认） | 32,768 | 32,768 | 32,768 |
| 单次请求超时 | 240 秒 | 600 秒 | 240 秒 |
| tool_choice | required | auto | auto |
| 每条轨迹最多步数 | 200 | 200 | 200 |
| 单条生成时限 | 7,200 秒 | 7,200 秒 | 3,000 秒 |

输入上限为 131,072；上下文达到 120,000 时压缩至目标 110,000，保留首尾各 5 轮。API SDK 内层 `num_retries=0`；框架对超时、连接异常、HTTP 408/429/5xx 做有界重试：single/TTS 最多 20 次尝试，当前 P0 author 最多 8 次尝试（含首次，即最多重试 7 次）。每次内部尝试，包括失败请求，都在原预算中计数。认证、参数及证书错误直接标记生成中断。

API 重试耗尽时保存 `api-interrupted.patch` 和轨迹，标记 `exit_api`，不自动提交、不进入审查，也不把部分补丁发布为完成结果。主执行器对可重试故障最多再做一次断点恢复，继续使用原模型及剩余调用数、步数和时间；代码快照失败时保留原容器。详见[恢复规则](docs/recovery.md)。

TTS summary 使用同一个模型，temperature=0.7、top_p=1。GLM/Qwen 输出上限为 8,192、请求超时为 120 秒；每轮最多 5 次传输尝试，格式不合格或传输耗尽后最多进行 3 轮，总上限为 15 次请求。GLM/Qwen 沿用历史格式校验：接受普通或加粗标题，以及带方括号的样本序号；必须包含 `BEST_SAMPLE`、`REASONING:`、`INSIGHTS FOR NEXT ATTEMPT:`，且总结至少 100 字符。Kimi 输出上限为 32,768、超时为 240 秒；每轮最多 3 次传输尝试，耗尽即停止；格式不合格最多 3 轮，并拒绝截断的 summary。

P0 多候选 summary 使用同模型、temperature=0、top_p=1；输出上限读取 `phoenix/settings.py` 中的模型配置，当前 GLM 为 32,768、读取超时 240 秒，Qwen 为 32,768、600 秒，连接超时均为 30 秒。受共享预算限制只调用一次，读取完整候选 diff，要求对照原始 issue、保留不确定性、避免合并不兼容方案；明确剩余预算用于最终 P0 生成。与 author 共用最短 2 秒的账户/模型请求间隔。非空 summary 交给 final，不使用 TTS 的额外格式门槛；单候选不经过 summary。

GLM/Qwen 支持三个入口；Kimi 支持 single/TTS。模型支持情况取决于你的服务商账号，不会用另一模型自动替代。

## 输出

- `plan.json`：无密钥的运行配置与输入指纹。
- `<model>/<instance_id>/report.json`：生成状态、步骤与调用数。
- `<model>/<instance_id>/final.patch`：最终生成补丁；我们的方法另存同内容的 `P0.patch`。
- 各阶段目录保留配置、日志、轨迹、候选与 summary，便于审计。
- `attempts/`、`execution.json`：原始尝试、恢复原因、累计预算及执行时限；`result.json` 保存已完成补丁哈希。恢复保留原稿，不覆盖为一次新生成。
- `api-usage.sqlite3`：框架取得的原始 API 用量账本。旧 `model_stats.tokens_received` 可能不含工具调用参数，不能作为完整计费用量。

运行输出、数据、结果文件和密钥由 `.gitignore` 排除。仓库提交方法代码、参数配置、合成单元测试和说明文档；聚合成绩直接列在 README 中。

## 验证与来源

```bash
python -m pytest -q
```

默认测试不访问模型 API。可另设 `PHOENIX_SMOKE_BASE_IMAGE`（本地已有、含 Git/Bash 的镜像）和 `PHOENIX_SMOKE_RUNTIME` 后运行 `python -m pytest tests/test_docker.py -q`，分别验证 GLM/Qwen 配置下的源码导出、完整定位、真实容器编辑、历史隔离和 P0 提交；该测试使用合成代码与固定动作回放，不调用模型。

离线测试覆盖参数加载与命令行入口、候选/summary/final 的共享预算、剩余额度转移、API 第八次尝试成功、重试耗尽、最后一次调用失败及恢复后的预算连续性。API 故障通过合成响应注入，不调用付费模型。

运行时保留有界的提交提取、binary diff、交互命令限制、持久化恢复状态和所有权校验。跨进程恢复不会重置已用预算；P0 停在首次有效提交，不执行后续监督/P1 阶段。镜像、依赖、网络条件及同模型定位由使用者提供，运行开始会固定镜像 digest；`pin_cpus` 和环境变量 `PHOENIX_CONTAINER_PROXY` 可用于本机环境配置。

核心 agent 来自 SWE-agent 1.1.0 的项目内改版；保留原 MIT LICENSE。TTS 提示词与原始实验的 sample/summary/final 逻辑一致。完整策略定位和 P0 author guidance 从对应方法快照提取；不包含按任务 ID 分支的修复规则、参考补丁或评测答案。公开版本将服务器专用路径和控制器替换为可配置入口。
