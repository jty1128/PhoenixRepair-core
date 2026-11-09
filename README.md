# PhoenixRepair Core

## 当前 GLM-5.2 代码与参数（2026-11-09）

`main` 和 `reproduce/glm-5.2` 同步方法代码。默认 `bash our_method.sh`、`configs/models/glm-5.2/our_method.yaml` 和[显式配置](configs/experiments/glm-5.2-p0-200calls-32k.yaml) 均设置 **GLM-5.2、每题 200 次共享调用、32,768 输出上限、4 并发**。

### 生成预算与监督阶段

**本版本只输出首次提交的 P0：独立 reviewer、审查后 repair、recheck 均为 0 次。** 作者自行检查代码、执行测试、修改实现所需的模型调用计入生成预算。原先为后续审查保留的 `10 + 25 + 5 = 40` 次已经全部释放；相应提示词和运行中的预算提醒也已更新。

| 定位候选数 | 调用分配 |
| --- | --- |
| 1 | author 最多 200 次 |
| 2 | 两个候选各 50 次 + summary 1 次 + final 至少 99 次 |
| 3 | 候选 34 / 33 / 33 次 + summary 1 次 + final 至少 99 次 |

候选未用完的额度转给 final；候选、summary、final 合计最多 200 次，API 失败和重试也计数。定位使用独立预算，可通过 `--locations` 复用已有的同模型定位。轨迹步骤和 API 调用次数是两个不同计数。

### 模型与运行参数

| 参数 | GLM-5.2 P0 200-call 版本 |
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
| 请求间隔 | 同账户、同模型最短 2 秒 |
| P0 summary | 最多 1 次请求；temperature=0，top_p=1；连接/读取超时 30/240 秒 |
| 题目并发 / 容器启动并发 / 命令并发 | 4 / 2 / 4 |
| 每个生成容器资源 | 2 CPU、6 GiB 内存 |
| 独立 reviewer / repair / recheck | 0 / 0 / 0 |

输入上限为 131,072，输出上限为每次响应 32,768。`p0_call_budget` 与 `max_output_tokens` 已接入配置、命令行、author、P0 summary 和 `--resume` 的配置一致性检查。

### 2026-11-08 的代码修复

- **API 超时和连接异常**：P0 对超时、连接中断及 HTTP 408/429/5xx 原地重试，单次 query 最多尝试 8 次；认证、参数、证书错误直接停止该生成。Single/TTS 的 query 上限保留 20 次。
- **耗尽重试的处理**：保存 `api-interrupted.patch`、错误类型与补丁哈希以及完整轨迹，标记 `exit_api`；不会把未完成代码自动提交为成功 P0，也不会开始后续审查。快照包含未跟踪文件，使用独立临时 Git index，保留原暂存区。
- **断点恢复**：可重试 API 故障在仍有调用、步数、时间余额时，最多额外进行一次阶段恢复，载入保存的代码和轨迹。若无法保存快照，保留原容器。`--resume` 沿用原输入、模型、预算和已用计数，不重新开始一份预算。
- **预算计数**：每次内部传输尝试前持久化计数，失败、重试以及已发出但结果未知的请求均占额度。最后一次额度消耗在失败请求上时，仍记录为 API 中断，不能转换为正常预算提交。
- **上下文和输出配置**：输入上限、压缩阈值、压缩目标、输出上限和 P0 调用预算均可显式设置，恢复时检查一致性。当前 GLM 默认值与上表及显式配置一致。

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

密钥通过本机 `PHOENIX_API_KEY` 或 `.env` 提供。省略 `--stage patch --locations ...` 会重新定位。公开入口不启动官方评测。旧的 160-call P0 可显式传入 `--p0-call-budget 160`；模型输出上限另用 `--max-output-tokens` 设置，变更后使用新输出目录。

## 方法入口

包含三种方法的核心代码、参数配置、运行说明和合成测试，基于 SWE-agent / SWE-ReX。实验数据、分数、用量统计、轨迹、生成补丁、日志、API 密钥和机器专用配置均保留在本机，不纳入提交。

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

## 模型配置与恢复

代码和模型配置统一维护在 `main`，`reproduce/glm-5.2` 同步当前代码及 GLM 默认配置。各模型配置在下列目录中，使用 `--config` 选择即可；也可用 `--model` 覆盖模型，但其余配置仍来自所选配置文件。

| 配置目录 | 默认模型 | 配置说明 |
| --- | --- | --- |
| [configs/models/glm-5.2](configs/models/glm-5.2) | GLM-5.2 | P0：200 次共享调用、32,768 输出；single/TTS：8,192 输出 |
| [configs/models/qwen3.7-plus](configs/models/qwen3.7-plus) | Qwen-3.7 Plus | 支持 single/TTS/P0；P0 保留 160 次共享调用配置 |
| [configs/models/kimi-k2.7-code](configs/models/kimi-k2.7-code) | Kimi K2.7 Code | 支持 single/TTS；无 P0 配置 |

```bash
bash run_tts.sh --config configs/models/glm-5.2/tts.yaml
bash our_method.sh --config configs/models/qwen3.7-plus/our_method.yaml
bash run_single.sh --config configs/models/kimi-k2.7-code/single.yaml
```

GLM 的 `our_method.yaml` 已更新为 200 calls / 32K；其 `single.yaml` 和 `tts.yaml` 显式保留历史 8,192 输出配置。Qwen/Kimi 继续使用各自配置；Kimi 的 `our_method.yaml` 明确拒绝运行。根目录下三个 bash 入口及默认配置继续可用。

`phoenix/recovery_profiles.py` 明确记录模型差异。GLM/Qwen 的 Single/TTS 在 7,140 秒停止生成，预留 60 秒提取当前补丁；Kimi 保留完整 3,000 秒生成时限，之后另用最多 60 秒提取。Kimi 保留 thinking/reasoning 回传。各模型的请求超时、输出上限和 summary 重试规则见下表及后文，不会在恢复时替换模型。

已移植镜像检查与固定 digest、环境启动重试、运行时兼容修复、命令/启动 I/O 闸门、可选自适应准入、超时补丁提取、轨迹续跑、跨容器提交重放和测试文件清理。提交重放只验证原补丁的文件内容与权限；没有新增模型审核、P1 或按题目定制的修复逻辑。

**TTS 与 P0 的预算单位不同，但都不因恢复重置。** TTS 的每个 sample/final 各自最多 200 步，summary 另有有界次数；P0 的候选、summary 和 final 共用 `p0_call_budget`。当前 GLM 为 200 次，历史配置为 160 次。若已用 100 次，当前版本最多再用 100 次，历史版本最多再用 60 次。未知请求仍计入消耗；重放/校验本身不调用模型。[完整恢复规则和验证方式](docs/recovery.md)。

## P0 的准确含义

这里的 `our_method.sh` 对应“审查前 P0”，不是旧的无生成指导版本，也不是审核后的 P1：

- 使用完整策略定位流程；每个候选应形成完整方案，并与原始需求对应。
- 生成时把定位视为待验证的建议，以原始 issue 为准；检查调用方、关联行为、边界情况和实际 diff。
- 当前 200-call 配置使用完整生成预算提示，删除原 40 次审查预留说明；历史 160-call 配置继续保留历史提示。本入口在首次提交后结束，**不调用审核模型，不进行审查后的修复，不选择 P1**。
- 当前单候选最多 200 次；多候选共用前 100 次，summary 一次，final 至少 99 次并继承上游节省的额度。历史 160-call 配置的 final 初始额度为 59 次。定位单独使用 150 步；`--localization-max-steps` 可显式调整，最多 200 步。
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

P0 多候选 summary 使用同模型、temperature=0、top_p=1；输出上限跟随 `max_output_tokens`，当前 GLM 为 32,768、读取超时 240 秒，Qwen 为 32,768、600 秒，连接超时均为 30 秒。受共享预算限制只调用一次，读取完整候选 diff，要求对照原始 issue、保留不确定性、避免合并不兼容方案；200-call 配置明确剩余预算用于最终 P0 生成。与 author 共用最短 2 秒的账户/模型请求间隔。非空 summary 交给 final，不使用 TTS 的额外格式门槛；单候选不经过 summary。

GLM/Qwen 支持三个入口；Kimi 支持 single/TTS。模型支持情况取决于你的服务商账号，不会用另一模型自动替代。

## 输出

- `plan.json`：无密钥的运行配置与输入指纹。
- `<model>/<instance_id>/report.json`：生成状态、步骤与调用数。
- `<model>/<instance_id>/final.patch`：最终生成补丁；我们的方法另存同内容的 `P0.patch`。
- 各阶段目录保留配置、日志、轨迹、候选与 summary，便于审计。
- `attempts/`、`execution.json`：原始尝试、恢复原因、累计预算及执行时限；`result.json` 保存已完成补丁哈希。恢复保留原稿，不覆盖为一次新生成。
- `api-usage.sqlite3`：框架取得的原始 API 用量账本。旧 `model_stats.tokens_received` 可能不含工具调用参数，不能作为完整计费用量。

运行输出、数据、结果汇总和密钥由 `.gitignore` 排除。仓库仅提交方法代码、参数配置、说明文档和合成单元测试。

## 验证与来源

```bash
python -m pytest -q
```

默认测试不访问模型 API。可另设 `PHOENIX_SMOKE_BASE_IMAGE`（本地已有、含 Git/Bash 的镜像）和 `PHOENIX_SMOKE_RUNTIME` 后运行 `python -m pytest tests/test_docker.py -q`，分别验证 GLM/Qwen 配置下的源码导出、完整定位、真实容器编辑、历史隔离和 P0 提交；该测试使用合成代码与固定动作回放，不调用模型。

离线测试覆盖参数加载与命令行入口、候选/summary/final 的共享预算、剩余额度转移、API 第八次尝试成功、重试耗尽、最后一次调用失败及恢复后的预算连续性。API 故障通过合成响应注入，不调用付费模型。

运行时保留有界的提交提取、binary diff、交互命令限制、持久化恢复状态和所有权校验。跨进程恢复不会重置已用预算；P0 停在首次有效提交，不执行后续监督/P1 阶段。镜像、依赖、网络条件及同模型定位由使用者提供，运行开始会固定镜像 digest；`pin_cpus` 和环境变量 `PHOENIX_CONTAINER_PROXY` 可用于本机环境配置。

核心 agent 来自 SWE-agent 1.1.0 的项目内改版；保留原 MIT LICENSE。TTS 提示词与原始实验的 sample/summary/final 逻辑一致。完整策略定位和 P0 author guidance 从对应方法快照提取；不包含按任务 ID 分支的修复规则、参考补丁或评测答案。公开版本将服务器专用路径和控制器替换为可配置入口。
