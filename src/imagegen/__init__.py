"""图像生成能力域：provider 插件门面与场景图资产接入。"""

from .service import IMAGEGEN_CAPABILITY, ImageGenError, SceneImageGenerator

__all__ = ["IMAGEGEN_CAPABILITY", "ImageGenError", "SceneImageGenerator"]
