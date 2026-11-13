# 提示词

这个仓库把口水话收成两家官方模型认的提示。规则来自公开的提示词指南，示例句子是我们自己写的，不搬官方样例。

## 静帧：Qwen-Image

依据是 [Qwen-Image `prompt_utils.py`](https://github.com/QwenLM/Qwen-Image/blob/main/src/examples/tools/prompt_utils.py) 里的中文改写器：

- 话太短就补主体、外貌、姿态、风格、空间关系和景别，原意留下来。
- 画面里要出现的字用引号包住，并写明位置。没让写的字不要加。
- 改写之后不出现否定词。用户说「不要路人」，提示里就既不写「不要」，也不写「路人」。
- 没指定风格时，照片用纪实摄影。
- 收成一段话，不要清单。
- 末尾使用官方改写器的固定后缀：`超清，4K，电影级构图`。
- 768×1344 和竖幅比例写在 `EmptySD3LatentImage` 上，不写进这段提示。后缀里的 4K 是 Qwen 改写器的用词，不是这张 16GB 卡的输出分辨率。

角色静帧、场景静帧、首帧都走这条。场景只描述地方本身。

## 视频：MiniMax H3 Ref2VA

依据是官方技能 [h3-prompt-writing](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md) 和 `references/ref-en.txt`。我们每条片子的每一镜都是全参考：首帧锁定，再加上这一镜自己的人物和场景。

六段顺序不能省：

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

具体要求：

- 六段用英文写。对白、歌词、画面上的字保留原语言。对白写成 `<d>[Chinese] ……</d>`，说话人是 `<Subject 1> (S1)`。
- `summary` 用方括号列出任务类型，用 ` + ` 连接，不重复。我们的普通镜头是 `keyframe completion + reference generation`。有台词再加 `audio reference`。只有同一动作延长才加 `video continuation`。
- 标签一经定义，全文同一个意思。`summary` 里不能新起标签。
- 只提供身份或场景的参考图，写进对应的 `<Subject>`，不单独占一个 `<Picture>`。单独的 `<Picture 1>` 只留给这一镜的首帧，`retention_analysis` 里标 `fully_preserved`，正文写 `the shot begins from <Picture 1>`。
- 场景保持为 `<Subject 2>`。不把场景静帧写成会锁死构图的独立画面。
- 可见参考的保留标记只用 `fully_preserved`、`partially_preserved`、`attribute_transfer`、`weak_reference`。声音标记只用 `fully_copy`、`partially_copy`、`reference`、`weak_reference`。新台词是 `reference`，不是把一条旧录音 `fully_copy`。`retention_analysis` 里不写 `(S1)`。
- `detailed_description` 先用一两句写风格和光线，再写 `[Shot 1]`。这一镜是一条 H3 成片，不再切第二镜，所以不加时间戳。镜头里写清构图、人物位置和长相、环境、动作、运镜、声音，以及参考在哪里生效。少用 cinematic、beautiful 这种空词。
- `overall_soundscape` 只写贯穿全片的环境声和物理声，不重复对白。
- `non_diegetic_music` 在 v0 写 `N/A`。

时长跟这一镜的 `17n+5` 网格一致，并写进 summary 和正文。

## 和任务文件的关系

分镜、首帧和视频任务里的提示都由上面两个函数生成。`生图` / `生视频` 直接返回这两段提示。`跑` 一条故事时，每一镜用同一套规则，再进入审核和隔离。
