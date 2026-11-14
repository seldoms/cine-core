"""16GB 卡上的实测契约。改分辨率或网格时，文档要一起改。"""

FPS = 24

# RTX 5060 Ti 16GB 上，H3 实际能跑的是 416×736，不是原生 720p。
VIDEO_WIDTH = 416
VIDEO_HEIGHT = 736

# 静帧。KSampler 的 latent 必须来自这个尺寸的 EmptySD3LatentImage。
STILL_WIDTH = 768
STILL_HEIGHT = 1344
LATENT_NODE = "EmptySD3LatentImage"
TEXT_ENCODE_NODE = "TextEncodeQwenImage21"

# 本地 H3 时长网格：frames = 17n + 5，24fps。
GRID_STRIDE = 17
GRID_OFFSET = 5
GRID_N_MIN = 5  # 90 帧，3.75 秒
GRID_N_MAX = 15  # 260 帧，约 10.83 秒

PROMPT_SECTIONS = (
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
)

TARGET_SECONDS = 300.0
MIN_SECONDS = 270.0
MAX_SECONDS = 330.0
