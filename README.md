# PhoenixRepair Core

## 实验结果

数据集：SWE-bench Pro v2，HARD-51（每组 51 题）。以下记录来自历史实验汇总，并非本次重新运行的结果。分数＝通过数（通过率）；Token/task＝平均每题 Token 消耗，单位为百万（M）。

| 实验 | GLM-5.2 分数 | Token/task | Qwen-3.7 Plus 分数 | Token/task |
| --- | ---: | ---: | ---: | ---: |
| Sample 1 | 33（64.71%） | 6.853M | 13（25.49%） | 3.444M |
| Sample 2 | 31（60.78%） | 6.584M | 16（31.37%） | 3.278M |
| Sample 3 | 30（58.82%） | 6.283M | 12（23.53%） | 3.536M |
| TTS Final | 33（64.71%） | ≥24.731M | 17（33.33%） | ≥13.169M |
| **我们的方法 · P0** | **32（62.75%）** | **4.228M** | **21（41.18%）** | **3.573M** |

**本仓库的 `bash our_method.sh` 对应上表的审查前 P0。** 生成指导在 P0 提交前已生效，提交后不执行额外审核或修订。TTS Final 的 `≥` 保留原统计口径，表示 Token 消耗下界。

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

每次运行使用新输出目录，避免覆盖已有结果或悄悄重置失败任务的预算。失败会保留日志并返回非零退出码；不会无限重试。输出 patch 不代表通过官方评测，本仓库不自动评测。

## P0 的准确含义

这里的 `our_method.sh` 对应“审查前 P0”，不是旧的无生成指导版本，也不是审核后的 P1：

- 使用完整策略定位流程；每个候选应形成完整方案，并与原始需求对应。
- 生成时把定位视为待验证的建议，以原始 issue 为准；检查调用方、关联行为、边界情况和实际 diff。
- 保留当时 P0 生成前的 author prompt 和预算提醒。原提示中的审核安排保留以匹配生成条件，但本入口在首次提交后结束，**不调用审核模型，不修订，不选择 P1**。
- 单候选的原始 200-call 配额中，P0 author 最多使用 160 次，原预留的 40 次不会被执行。多候选时，候选共享前半预算，summary 使用一次调用，final 可使用上游节省的额度，同时保留原 40 次预留。定位另有最多 200 步。
- 提交阶段只执行确定性的补丁校验；如仅混入测试文件修改，保留原稿并排除测试 hunks，不改变生产代码 hunks，不产生额外模型请求。

也可通过 `--locations` 复用自己已有的同模型定位。目录必须含 `instances.json`，以及 `<model>/<instance_id>/location_candidates.json` 和 `report.json`；会检查题目、base commit、模型及候选完整性。仓库不携带历史定位数据，因此重新生成不保证复现某次历史运行的具体分数。

## 模型设定

| 生成参数 | GLM-5.2 | Qwen-3.7 Plus | Kimi K2.7 Code |
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

Summary 使用同一个模型，temperature=0.7、top_p=1。GLM/Qwen summary 输出上限为 8,192；Kimi 为 32,768。GLM/Qwen summary 请求超时为 120 秒、最多 5 次传输尝试；Kimi 为 240 秒、最多 3 次，并拒绝截断的 summary。P0 多候选 summary 受共享预算限制，只允许一次尝试。

GLM/Qwen 支持三个入口；Kimi 支持 single/TTS。模型支持情况取决于你的服务商账号，不会用另一模型自动替代。

## 输出

- `plan.json`：无密钥的运行配置与输入指纹。
- `<model>/<instance_id>/report.json`：生成状态、步骤与调用数。
- `<model>/<instance_id>/final.patch`：最终生成补丁；我们的方法另存同内容的 `P0.patch`。
- 各阶段目录保留配置、日志、轨迹、候选与 summary，便于审计。
- `api-usage.sqlite3`：框架取得的原始 API 用量账本。旧 `model_stats.tokens_received` 可能不含工具调用参数，不能作为完整计费用量。

运行输出、数据和密钥全部由 `.gitignore` 排除。维护时只提交代码、配置模板和合成单元测试。

## 验证与来源

```bash
python -m pytest -q
```

默认测试不访问模型 API。可另设 `PHOENIX_SMOKE_BASE_IMAGE`（本地已有、含 Git/Bash 的镜像）和 `PHOENIX_SMOKE_RUNTIME` 后运行 `python -m pytest tests/test_docker.py -q`，验证真实容器中的编辑、历史隔离和 P0 提交；该测试使用合成代码与固定动作回放，不调用模型。

核心 agent 来自 SWE-agent 1.1.0 的项目内改版；保留原 MIT LICENSE。TTS 提示词与原始实验的 sample/summary/final 逻辑一致。完整策略定位和 P0 author guidance 从对应方法快照提取；不包含按任务 ID 分支的修复规则、参考补丁或评测答案。公开版本将服务器专用路径和控制器替换为可配置入口。
