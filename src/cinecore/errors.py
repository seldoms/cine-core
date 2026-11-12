class CineError(Exception):
    """调用方可以接住的预期错误。"""


class VerbatimOverlap(CineError):
    def __init__(self, fragment: str):
        super().__init__(f"改编结果与原文连续片段重合，已拒绝：{fragment}")
        self.fragment = fragment


class ResumeInterrupt(CineError):
    """测试或外部停止用。进度已经落盘，不算这条故事失败。"""
