"""SAM box tightening and point → box for urchin labels (PREREG "Labels").

SAM (facebook/sam-vit-base, Apache-2.0) works at 1024 px, so each prompt runs on a crop around
it (context, at least CROP_MIN px) to keep small urchins at full resolution. The returned box is
the tight bounding box of the mask, in full-image pixels. Results are checked on overlays.
"""

from __future__ import annotations

import numpy as np

MODEL_ID = "facebook/sam-vit-base"
CROP_MIN = 256
BOX_CONTEXT = 3.0  # crop side = 3 × the box's long side
POINT_CROP = 384


def crop_window(cx: float, cy: float, side: float, width: int, height: int) -> tuple[int, ...]:
    """Square crop (x0, y0, x1, y1) of the given side centred on (cx, cy), shifted to fit."""
    side = int(min(max(side, CROP_MIN), width, height))
    x0 = int(round(min(max(cx - side / 2, 0), width - side)))
    y0 = int(round(min(max(cy - side / 2, 0), height - side)))
    return x0, y0, x0 + side, y0 + side


def mask_box(mask: np.ndarray) -> list[float] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]


class SamRefiner:
    def __init__(self, device: str | None = None, model_id: str = MODEL_ID):
        import torch
        from transformers import SamModel, SamProcessor

        self.torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.processor = SamProcessor.from_pretrained(model_id)
        self.model = SamModel.from_pretrained(model_id).to(self.device).eval()

    def _run(self, rgb_crop: np.ndarray, **prompt) -> tuple[np.ndarray, np.ndarray]:
        inputs = self.processor(images=rgb_crop, return_tensors="pt", **prompt)
        sizes = (inputs["original_sizes"], inputs["reshaped_input_sizes"])
        # MPS has no float64; the processor returns float64 prompt tensors.
        feed = {
            k: (v.float() if v.dtype == self.torch.float64 else v).to(self.device)
            for k, v in inputs.items()
        }
        with self.torch.no_grad():
            out = self.model(**feed, multimask_output=True)
        masks = self.processor.image_processor.post_process_masks(out.pred_masks.cpu(), *sizes)[0][
            0
        ]  # (3, H, W) for the single prompt
        return masks.numpy(), out.iou_scores.cpu().numpy()[0, 0]

    def refine_box(self, rgb: np.ndarray, box: list[float]) -> dict:
        """Tighten one box: SAM with the box prompt; best predicted-IoU mask → bbox."""
        h, w = rgb.shape[:2]
        x1, y1, x2, y2 = box
        side = BOX_CONTEXT * max(x2 - x1, y2 - y1)
        cx0, cy0, cx1, cy1 = crop_window((x1 + x2) / 2, (y1 + y2) / 2, side, w, h)
        crop = np.ascontiguousarray(rgb[cy0:cy1, cx0:cx1])
        local = [[[x1 - cx0, y1 - cy0, x2 - cx0, y2 - cy0]]]
        masks, iou = self._run(crop, input_boxes=local)
        k = int(np.argmax(iou))
        b = mask_box(masks[k])
        if b is None:
            return {"box": list(box), "sam_iou": float(iou[k]), "sam_ok": False}
        return {
            "box": [b[0] + cx0, b[1] + cy0, b[2] + cx0, b[3] + cy0],
            "sam_iou": float(iou[k]),
            "sam_ok": True,
        }

    def point_box(self, rgb: np.ndarray, x: float, y: float) -> dict:
        """Box for an urchin marked by one point on it (missed by the pre-labeller)."""
        h, w = rgb.shape[:2]
        cx0, cy0, cx1, cy1 = crop_window(x, y, POINT_CROP, w, h)
        crop = np.ascontiguousarray(rgb[cy0:cy1, cx0:cx1])
        masks, iou = self._run(crop, input_points=[[[[x - cx0, y - cy0]]]])
        k = int(np.argmax(iou))
        b = mask_box(masks[k])
        if b is None:
            return {"box": None, "sam_iou": float(iou[k]), "sam_ok": False}
        return {
            "box": [b[0] + cx0, b[1] + cy0, b[2] + cx0, b[3] + cy0],
            "sam_iou": float(iou[k]),
            "sam_ok": True,
        }
