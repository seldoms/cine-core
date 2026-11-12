# 提示契约

## 视频：六段，不能省成四段

H3 Ref2VA 的默认提示按这个顺序写满六段：

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

`summary` 用方括号写出任务类型。本仓库的普通镜头是 `keyframe completion` 加 `reference generation`。有台词时再加 `audio reference`。只有同一动作的延长才加 `video continuation`。

`retention_analysis` 里，可见参考用 `fully_preserved`、`partially_preserved`、`attribute_transfer`、`weak_reference`。声音不用「整轨复制」来冒充新台词；v0 的新台词标成 `reference`。

标签只定义这一镜真正用到的东西：入画的人、首帧、这一镜的场景，以及在需要时的上一段视频和一句台词。不要为了整齐把没入画的角色也写进去。

四段简写不会通过 `prompt_contract` 检查。

## 参考图

每个镜头的 `reference_plan` 只列这一镜的角色静帧和场景静帧。首帧在审核通过后单独锁进任务的 `first_frame`，不把素材库里其余的服装和人物一起送进去。

画外人物可以出现在视觉圣经里，但不会生成参考图，也不会出现在镜头任务里。

## 静帧

角色静帧、场景静帧和首帧都走同一条 latent 契约：`EmptySD3LatentImage`，768×1344。工作流模板在 `workflows/still_qwen_image.api.json`。它记录的是接法，不是某一台机器上可以直接跑的完整图：检查点、VAE 和本地节点名仍要按你的 ComfyUI 填。

## 视频图

`workflows/video_h3_ref2va.api.json` 里的 `H3Ref2VASampler` 是占位类名。本地包的真实类名写到 `comfyui.node_classes.video_sampler`。图里只加载首帧和这一镜的参考图。帧数必须落在 `17n+5` 上，尺寸是 416×736。

尾帧输入只在 `continuation.mode = same_action_extend` 且同一场、同一动作组时出现。换一场就不要接。
