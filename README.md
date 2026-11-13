# cine-core

**English.** cine-core is a research core for unattended production of AI short films of about five minutes, adapted from a story corpus. It is not a short-drama serial pipeline, and v0 does not yet produce cinematic results unattended. The runnable path is a dry-run: every stage executes, GPU calls are stubbed, failures are audited and quarantined, and the overnight loop continues. The current hardware target is a 16 GB RTX 5060 Ti running local ComfyUI.

## 这是什么

这个项目要做的事，是让生图和生视频变简单：嘴里一句口水话，出去就是 Qwen-Image 和 MiniMax H3 各自认的提示词，用来做照片或者一条短片。生图走 Qwen-Image 的官方改写规则，生视频走 H3 Ref2VA 的六段规则。卡的速度仍然有限，提示词才是这里要维护的核心。

它也是 **24 小时无人值守地生产大约 5 分钟 AI 短片** 的研究内核。

素材来自故事语料（社会新闻、感人小品、有趣短篇这一类由头）。系统把一条素材改编成一部能看完的短片，而不是把原文念出来，也不是把它写成连续剧。人负责更新语料，并在事后抽查隔离出来的失败。人不是每个镜头的裁判。

v0 把这条循环做成了可以跑、可以测的核心：摄入、改编、带版本的素材、分镜、首帧锁定、多次 take、自动审核、隔离、合成契约、崩溃后续跑。渲染器收在后端后面，以后换更强的语言模型或视频模型，不应该重写阶段图。

## 这不是什么

- 不是短剧流水线，也不按集往下写。
- 不是已经能无人值守产出电影级成片的系统。v0 是可运行的核心和骨架。
- dry-run 不生成画面母带。GPU 调用是打桩的。示例里有一条故意失败的镜头，用来走通隔离，不是画质评估。
- 不把云端视频 API 当默认路径。配置示例里没有密钥。

## 硬件事实

当前目标机器是 **16GB 的 RTX 5060 Ti**，本地 ComfyUI，视频是 MiniMax H3 Ref2VA，静帧是 Qwen-Image 这一路。

- 这张卡上 H3 实际跑的是 **416×736**，不是原生 720p。放大到 720×1280 只是后期，会更软；v0 不执行这个放大。
- 静帧是 **768×1344**。`KSampler` 的 latent 必须来自 `EmptySD3LatentImage`，不能拿 `TextEncodeQwenImage21` 的 1024 方形 latent。
- 时长吸附到 **17n+5** 帧、24fps。例如 90 帧 = 3.75 秒，124 帧约 5.17 秒，260 帧约 10.83 秒。口头说的 208 帧不在公式上（公式是 209），v0 不用 208。
- 静帧大约 20 秒一张；5.17 秒的视频大约 250–280 秒；接近 8.7 秒的视频大约 600 秒。一部五分钟短片要好几个小时，重拍会更久。一张卡一天做不了多少部。
- 视频模型没有跨镜头记忆。每镜只用自己的参考图。先审首帧，再出视频。尾帧只延长同一个动作。
- 手不可靠，分镜禁止手部特写。这个宽度里尽量只放一个人。
- 台词堆在一个镜头里容易变成旁白；单人长句会读错；「他」和「她」会在识别里撞车。v0 只做审核钩子，不假装已经修好。

细节在 [docs/hardware-16g.md](docs/hardware-16g.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/cinecore/` | 管道、审核、隔离、dry-run 和 ComfyUI 客户端 |
| `schemas/` | 语料、剧本、圣经、分镜、镜头任务、审核报告、运行日志 |
| `samples/corpus/` | 三条原创虚构小品，不是爬来的新闻 |
| `docs/` | 研究目标、流程、硬件、审核、提示契约 |
| `workflows/` | 静帧和视频的 API 图模板，记录契约，不是某个私人工作流的拷贝 |
| `config.example.yaml` | 可以直接拿来 dry-run 的配置 |

## 安装和 dry-run

在仓库根目录：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
cine run --mode unattended --dry-run --config config.example.yaml --source samples/corpus --requeue
```

也可用 `python -m cinecore`。需要 Python 3.11 以上。

跑完会打印 `run_dir=runs/<run_id>`。里面有：

- 每条故事的 `screenplay.json`、`bible.json`、`storyboard.json`
- `library.json` 和每个镜头的 `job.json`、首帧记录、take、审核报告
- `sample-market-rain` 第 4 镜的隔离文件（示例配置注入的手部缺陷，重拍后仍失败）
- `assemble/concat_plan.json`、`subtitles.srt`、`film_sidecar.json`
- 运行目录下的 `run_log.json` 和 `state.json`

退出码 0 只表示循环走完。成片有没有通过，看 `run_log.json` 里每条故事的 `status`，以及审核报告的 `passed`。`cine audit --run-dir runs/<run_id>` 也是这样：它退出 0 只代表汇总写完了。

其他命令：

```bash
cine ingest --source samples/corpus --dest data/corpus.sqlite
cine plan --story sample-paper-boat --source samples/corpus --out runs/preview --config config.example.yaml
cine status --run-dir runs/<run_id>
cine run --resume runs/<run_id> --dry-run --source samples/corpus
```

第一次 dry-run 之后，故事会记进 `data/ledger.json`，下次默认跳过。再跑加 `--requeue`。

## 从即时消息发起

分步的 `ingest` / `plan` / `run` 还在，但发起一条任务不必走它们。聊天工具把用户原文 POST 到本机，回复的中心是六段提示词。细节在 [docs/im.md](docs/im.md)。

```bash
cine serve --config config.example.yaml
```

```bash
curl -s http://127.0.0.1:8787/tasks \
  -H 'Content-Type: application/json' \
  -d '{"text":"跑 sample-paper-boat"}'
```

没有机器人时也可以：

```bash
cine task --config config.example.yaml --text '生图 雨里发呆的短发女人'
cine task --config config.example.yaml --text '生视频 她把灯挂上铁钩，说「到此为止」'
cine task --config config.example.yaml --text '跑 sample-paper-boat'
```

这条入口固定 dry-run。真卡要跑数小时，不能挂在一条聊天连接上；换了入口也不会让 16GB 卡变快。

示例语料是为本仓库写的虚构文本。`sample-market-rain` 的体裁是 `news`，但正文不是真实报道，改编器也不会把它当口播念出来。

## 以后接到本地 ComfyUI

1. 复制 `.env.example` 为 `.env`，或直接导出环境变量。主机只用你自己的地址，例如 `export CINE_COMFYUI_HOST=http://127.0.0.1:8188`。不要把局域网地址写进仓库。
2. 本地节点类名和配置里的 `comfyui.node_classes` 对不上时，改配置，不要改阶段图。视频采样器的类名 `H3Ref2VASampler` 是占位。
3. 每个镜头任务里的 `workflow.keyframe` 和 `workflow.video` 已经按契约接好，可以拿去对照本地图。静帧若仍把 latent 接到文本编码器，用代码里的 `enforce_still_latent_contract` 改线。

v0 **不会**因为你填了主机就开始向 ComfyUI 提交整夜任务。这一版还不能把 history 收成可靠的画面文件；批处理若发现要走真实 ComfyUI，会直接拒绝启动，避免把收不回来的作业推进队列。HTTP 客户端（`/prompt` 和 `/history`）在代码里，给下一版接上收文件用。当前要看完整循环，请继续用 `--dry-run`。

## 测试

```bash
pytest
```

GitHub Actions 里也只跑测试。覆盖时长网格、审核和隔离策略、schema、示例语料的 dry-run、中断后续跑，以及即时消息发起任务。

## 许可

[MIT](LICENSE)。
