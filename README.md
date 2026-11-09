# PhoenixRepair

## Overall Performance

以下为 **SWE-bench Pro v2 HARD-51** 的成绩汇总，每组 51 题

| 方法 | GLM-5.2 | Qwen-3.7 Plus | Kimi K2.7 Code |
| --- | ---: | ---: | ---: |
| Sample 1 | 31/51（60.78%） | 13/51（25.49%） | 29/51（56.86%） |
| Sample 2 | 33/51（64.71%） | 16/51（31.37%） | 33/51（64.71%） |
| Sample 3 | 31/51（60.78%） | 12/51（23.53%） | 33/51（64.71%） |
| TTS Final | 33/51（64.71%） | 17/51（33.33%） | 34/51（66.67%） |
| **Our Method · P0** | **35/51（68.63%）** | **21/51（41.18%）** | 未运行 |

### tokens计费

下表列出 **GLM、Qwen 和 Kimi** 的实验成绩与消耗；Kimi 的 Our Method · P0 尚未运行。每 task 均为对应实验 51 题的平均值，包含未通过评测的题。**Input 已包含缓存命中；总 tokens = input + output tokens。**

- **Sample 1/2/3**：各自生成一个样本的成本。
- **TTS Final**：三个 samples＋同模型 summary＋final 生成的完整流程成本，不是只计算最后一段生成。
- **Our Method · P0**：定位＋候选生成＋必要的 summary＋首次 P0 提交前的 final 生成，包含复用定位原本的成本。

按阿里云百炼北京地域在线调用公开价格估算，下列单价及费用统一使用 **美元（USD）**。沿用本次审计的固定汇率 **1 USD = 6.7168 CNY** 将人民币公开价除以 6.7168 换算。Qwen 本表请求均已核对为单次输入 ≤256K，采用隐式缓存价格。

| 模型 | 非缓存 input（USD / 百万 tokens） | 缓存命中 input（USD / 百万 tokens） | Output（USD / 百万 tokens） |
| --- | ---: | ---: | ---: |
| [GLM-5.2](https://help.aliyun.com/zh/model-studio/glm-5-2) | $1.1910 | $0.2978 | $4.1687 |
| [Qwen-3.7 Plus（输入 ≤256K）](https://help.aliyun.com/zh/model-studio/qwen3-7-plus) | $0.2978 | $0.0596 | $1.1910 |
| [Kimi K2.7 Code](https://help.aliyun.com/zh/model-studio/kimi-k2-7-code) | 普通输入 $0.9677<br>显式缓存创建 $1.2097 | 隐式缓存 $0.1935<br>显式缓存 $0.0968 | $4.0198 |

Kimi 的显式缓存创建费用计入非缓存 input 费用。表中美元单价保留四位小数，费用计算使用未四舍五入的换算单价。

```text
非缓存 input = input - 缓存命中 input
GLM / Qwen 费用（USD）= ((input - cache) × 非缓存输入美元单价 + cache × 缓存美元单价 + output × 输出美元单价) / 1,000,000
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


### Kimi K2.7 Code：实验消耗

| 实验 | 成绩 | 总 tokens / task | Input tokens / task（含缓存） | 缓存 input tokens / task | 非缓存 input tokens / task | Output tokens / task |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | 29/51 | 5,508,086.80 | 5,467,148.73 | 5,273,381.33 | 193,767.39 | 40,938.08 |
| Sample 2 | 33/51 | 5,670,231.37 | 5,627,072.24 | 5,423,574.47 | 203,497.76 | 43,159.14 |
| Sample 3 | 33/51 | 5,691,051.27 | 5,648,908.16 | 5,452,381.75 | 196,526.41 | 42,143.12 |
| TTS Final | 34/51 | 22,233,841.78 | 22,055,256.69 | 21,242,491.20 | 812,765.49 | 178,585.10 |

分项计费（美元 USD）：

| 实验 | 非缓存 input 费用 / task | 缓存 input 费用 / task | Output 费用 / task | 合计费用 / task | 51 题总费用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sample 1 | $0.1878 | $1.0205 | $0.1646 | $1.3729 | $70.02 |
| Sample 2 | $0.1972 | $1.0495 | $0.1735 | $1.4202 | $72.43 |
| Sample 3 | $0.1904 | $1.0551 | $0.1694 | $1.4149 | $72.16 |
| TTS Final | $0.7877 | $4.1107 | $0.7179 | $5.6163 | $286.43 |

## 安装

要求 Linux x86_64、Python 3.11+、Git，以及当前账号可访问的 Docker daemon。
请克隆本仓库后使用下面的 editable 安装方式；入口会读取源码树中的工具和配置。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,runtime]'
[ -f .env ] || cp .env.example .env
```

在 `.env` 中填写任务、密钥和运行时路径；三个入口共用这些配置：

```dotenv
PHOENIX_API_KEY="你的 API key"
INSTANCES="/绝对路径/instances.json"
PHOENIX_RUNTIME="/绝对路径/PhoenixRepair-core/.runtime"
```

任务清单支持 JSON 数组或 JSONL，每题必须包含 `instance_id`、`problem_statement`、`base_commit`、`repo_name`、`image_name` 五个字段。API 地址可用 `API_BASE` 设置；独立 Docker daemon 可用 `DOCKER_HOST` 设置。密钥只保存在本机环境变量或 `.env` 中。

任务镜像需事先拉取，且含有对应仓库的原始 base checkout。方法不自动下载或注入官方评测材料。

## 准备工具运行时

已有兼容的独立 Python / SWE-ReX 运行时，可直接配置 `PHOENIX_RUNTIME`。首次安装可执行：

```bash
python -m phoenix.runtime prepare --output "$PWD/.runtime"
python -m phoenix.runtime check --output "$PWD/.runtime"
```

准备过程下载独立 Python 3.11 和工具依赖，因此需要联网。生成时只把工具运行时挂入容器，不替换任务仓库自己的 Python/Node/Go 环境。`.runtime` 不上传 Git。

## 运行

三个统一入口根据 **方法 + `--model`** 自动加载对应实验配置，无需手动选择 `--config`。以下命令在仓库根目录执行，使用上面配置的同一份任务清单。

| 入口 | 执行流程 |
| --- | --- |
| `run_single.sh` | 每题生成一个独立 sample |
| `run_tts.sh` | 每题生成三个独立 samples → 同模型 summary → 从原始代码生成 final patch |
| `our_method.sh` | 完整策略定位 → 候选生成 → 必要的 summary 与 final → 输出审查前 P0 |

### Sample 1 / 2 / 3

分别使用三个输出目录运行：

```bash
# GLM-5.2
bash run_single.sh --model glm5.2 --output runs/glm-sample-1
bash run_single.sh --model glm5.2 --output runs/glm-sample-2
bash run_single.sh --model glm5.2 --output runs/glm-sample-3

# Qwen-3.7 Plus
bash run_single.sh --model qwen3.7-plus --output runs/qwen-sample-1
bash run_single.sh --model qwen3.7-plus --output runs/qwen-sample-2
bash run_single.sh --model qwen3.7-plus --output runs/qwen-sample-3

# Kimi K2.7 Code
bash run_single.sh --model kimi-k2.7-code --output runs/kimi-sample-1
bash run_single.sh --model kimi-k2.7-code --output runs/kimi-sample-2
bash run_single.sh --model kimi-k2.7-code --output runs/kimi-sample-3
```

### TTS Final

```bash
bash run_tts.sh --model glm5.2 --output runs/glm-tts
bash run_tts.sh --model qwen3.7-plus --output runs/qwen-tts
bash run_tts.sh --model kimi-k2.7-code --output runs/kimi-tts
```

TTS 命令执行完整流程，自行生成三个 samples，不自动读取上述 Single 输出。

### Our Method · P0

```bash
bash our_method.sh --model glm5.2 --output runs/glm-p0
bash our_method.sh --model qwen3.7-plus --output runs/qwen-p0
```

命令先定位再生成，最终停在 P0，不执行后续审查或修复。Kimi P0 尚未运行，当前不提供对应实验入口。

### 检查、并发与恢复

```bash
# 查看最终生效配置；不调用模型、不启动容器
bash run_single.sh --model glm5.2 --dry-run
bash run_tts.sh --model kimi-k2.7-code --dry-run
bash our_method.sh --model qwen3.7-plus --dry-run

# 同时处理 4 道题
bash run_single.sh --model glm5.2 --output runs/glm-sample-1 --concurrency 4

# 中断后沿用原命令、配置和输出目录，加 --resume 继续
bash run_tts.sh --model kimi-k2.7-code --output runs/kimi-tts --resume
```

新实验使用新输出目录。`--limit 6` 可先运行少量任务；`--help` 查看全部参数。恢复沿用已消耗的调用、步骤和时间预算。生成补丁后需另行官方评测。

模型参数保存在 [configs/models](configs/models)，可用 `--dry-run` 查看本次解析后的配置。更多细节见 [GLM P0 参数](docs/glm-p0-parameters.md)与[恢复规则](docs/recovery.md)。

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

离线测试覆盖参数加载与命令行入口、候选/summary/final 的共享预算、剩余 API 调用次数转移、API 第八次尝试成功、重试耗尽、最后一次调用失败及恢复后的预算连续性。API 故障通过合成响应注入，不调用付费模型。

运行时保留有界的提交提取、binary diff、交互命令限制、持久化恢复状态和所有权校验。跨进程恢复不会重置已用预算；P0 停在首次有效提交，不执行后续监督/P1 阶段。镜像、依赖、网络条件及同模型定位由使用者提供，运行开始会固定镜像 digest；`pin_cpus` 和环境变量 `PHOENIX_CONTAINER_PROXY` 可用于本机环境配置。

核心 agent 来自 SWE-agent 1.1.0 的项目内改版；保留原 MIT LICENSE。TTS 提示词与原始实验的 sample/summary/final 逻辑一致。完整策略定位和 P0 author guidance 从对应方法快照提取；不包含按任务 ID 分支的修复规则、参考补丁或评测答案。公开版本将服务器专用路径和控制器替换为可配置入口。

## DeepSeek V4.1 Flash

Flash P0 也使用统一入口，沿用 GLM P0 的预算与流程：

```bash
bash our_method.sh --model deepseek-v4.1-flash --output runs/flash-p0 --dry-run
bash our_method.sh --model deepseek-v4.1-flash --output runs/flash-p0
```

使用具有该模型访问权限的 API key，其余环境准备同上。
