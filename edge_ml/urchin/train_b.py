"""Model B of the urchin study: Faster R-CNN MobileNetV3-FPN fine-tuned on Reef Check stills.

    python -m edge_ml.urchin.train_b --train train.json --images <stills_root> --out <run_dir> \
        --seed 0 --predict val.json test.json

Fixed recipe (PREREG "Models"): torchvision fasterrcnn_mobilenet_v3_large_fpn, COCO weights
(BSD-3), new 2-class head; SGD lr 0.005, momentum 0.9, weight decay 1e-4, batch 4, 24 epochs,
x0.1 at epochs 16 and 22; horizontal flip, brightness/contrast jitter +-0.2; torchvision
default sizes 800/1333. Ignore boxes are left out of the training targets (background).

Writes <run_dir>/model.pt, train_log.jsonl, run.json and one detections file per --predict
label file: pred_<name>.json in owl.py's format, scores down to 0.01 (so the threshold can be
chosen on validation). Run at `nice 10`; torch CPU threads are capped at 2 (PREREG compute).
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import random
import sys
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("urchin.train_b")

RECIPE = {
    "arch": "fasterrcnn_mobilenet_v3_large_fpn",
    "weights": "COCO_V1",
    "lr": 0.005,
    "momentum": 0.9,
    "weight_decay": 1e-4,
    "batch_size": 4,
    "epochs": 24,
    "milestones": [16, 22],
    "gamma": 0.1,
    "hflip_p": 0.5,
    "brightness": 0.2,
    "contrast": 0.2,
    "min_size": 800,
    "max_size": 1333,
    "score_floor": 0.01,
    "detections_per_img": 300,
}


def load_coco(path: Path) -> tuple[list[dict], dict[int, list[list[float]]]]:
    coco = json.loads(path.read_text())
    boxes: dict[int, list[list[float]]] = {im["id"]: [] for im in coco["images"]}
    for a in coco["annotations"]:
        if a.get("ignore"):
            continue
        x, y, w, h = a["bbox"]
        if w > 1 and h > 1:
            boxes[a["image_id"]].append([x, y, x + w, y + h])
    return coco["images"], boxes


def build_model(device: str):
    import torchvision
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    weights = torchvision.models.detection.FasterRCNN_MobileNet_V3_Large_FPN_Weights.COCO_V1
    model = torchvision.models.detection.fasterrcnn_mobilenet_v3_large_fpn(
        weights=weights,
        min_size=RECIPE["min_size"],
        max_size=RECIPE["max_size"],
        box_score_thresh=RECIPE["score_floor"],
        box_detections_per_img=RECIPE["detections_per_img"],
    )
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, 2)
    return model.to(device)


def seed_all(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_image(path: Path):
    import torch
    from torchvision.io import ImageReadMode, decode_image

    img = decode_image(str(path), mode=ImageReadMode.RGB)
    return img.to(torch.float32) / 255.0


def augment(img, boxes, rng: random.Random):
    import torch

    if rng.random() < RECIPE["hflip_p"]:
        img = img.flip(-1)
        w = img.shape[-1]
        if len(boxes):
            boxes = torch.stack([w - boxes[:, 2], boxes[:, 1], w - boxes[:, 0], boxes[:, 3]], 1)
    b = 1 + rng.uniform(-RECIPE["brightness"], RECIPE["brightness"])
    c = 1 + rng.uniform(-RECIPE["contrast"], RECIPE["contrast"])
    mean = img.mean()
    img = ((img * b - mean * b) * c + mean * b).clamp(0, 1)
    return img, boxes


def train(args: argparse.Namespace) -> int:
    import torch

    torch.set_num_threads(2)
    device = args.device or ("mps" if torch.backends.mps.is_available() else "cpu")
    seed_all(args.seed)
    rng = random.Random(args.seed)
    images, boxes = load_coco(args.train)
    n_boxes = sum(len(v) for v in boxes.values())
    log.info(
        "train: %d stills, %d boxes, seed %d, device %s", len(images), n_boxes, args.seed, device
    )
    model = build_model(device)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.SGD(
        params, lr=RECIPE["lr"], momentum=RECIPE["momentum"], weight_decay=RECIPE["weight_decay"]
    )
    sched = torch.optim.lr_scheduler.MultiStepLR(opt, RECIPE["milestones"], RECIPE["gamma"])
    args.out.mkdir(parents=True, exist_ok=True)
    logf = (args.out / "train_log.jsonl").open("w")
    epochs = args.epochs or RECIPE["epochs"]
    t_start = time.monotonic()
    for epoch in range(epochs):
        model.train()
        order = list(range(len(images)))
        rng.shuffle(order)
        losses, t0 = [], time.monotonic()
        for i in range(0, len(order), RECIPE["batch_size"]):
            batch_imgs, targets = [], []
            for j in order[i : i + RECIPE["batch_size"]]:
                im = images[j]
                img = load_image(args.images / im["file_name"])
                b = torch.tensor(boxes[im["id"]], dtype=torch.float32).reshape(-1, 4)
                img, b = augment(img, b, rng)
                batch_imgs.append(img.to(device))
                targets.append(
                    {
                        "boxes": b.to(device),
                        "labels": torch.ones(len(b), dtype=torch.int64, device=device),
                    }
                )
            loss_dict = model(batch_imgs, targets)
            loss = sum(loss_dict.values())
            if not torch.isfinite(loss):
                raise RuntimeError(f"non-finite loss at epoch {epoch} batch {i}: {loss_dict}")
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(loss.item())
        sched.step()
        rec = {
            "epoch": epoch + 1,
            "loss": round(float(np.mean(losses)), 4),
            "lr": opt.param_groups[0]["lr"],
            "s": round(time.monotonic() - t0, 1),
        }
        logf.write(json.dumps(rec) + "\n")
        logf.flush()
        log.info("epoch %d/%d loss %.4f (%.0f s)", epoch + 1, epochs, rec["loss"], rec["s"])
    logf.close()
    torch.save(model.state_dict(), args.out / "model.pt")
    train_s = time.monotonic() - t_start

    for label_file in args.predict or []:
        preds = predict(model, label_file, args.images, device)
        (args.out / f"pred_{label_file.stem}.json").write_text(json.dumps(preds, indent=1))
    (args.out / "run.json").write_text(
        json.dumps(
            {
                "recipe": RECIPE,
                "epochs_run": epochs,
                "seed": args.seed,
                "device": device,
                "train": str(args.train),
                "n_train_stills": len(images),
                "n_train_boxes": n_boxes,
                "train_seconds": round(train_s, 1),
                "torch": torch.__version__,
                "host": platform.node(),
                "argv": sys.argv,
            },
            indent=1,
        )
    )
    log.info("done in %.0f s → %s", train_s, args.out)
    return 0


def predict(model, label_file: Path, images_root: Path, device: str) -> dict:
    import torch

    model.eval()
    images, _ = load_coco(label_file)
    out = {}
    with torch.no_grad():
        for im in images:
            img = load_image(images_root / im["file_name"]).to(device)
            r = model([img])[0]
            out[im["file_name"]] = [
                {"box": [round(float(v), 1) for v in b], "score": round(float(s), 4)}
                for b, s in zip(r["boxes"].cpu().numpy(), r["scores"].cpu().numpy(), strict=True)
            ]
    return {"model": f"B:{RECIPE['arch']}", "min_score": RECIPE["score_floor"], "detections": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--train", type=Path, required=True, help="COCO labels of training stills")
    ap.add_argument("--images", type=Path, required=True, help="stills root (file_name base)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--predict", type=Path, nargs="*", help="COCO label files to predict on")
    ap.add_argument("--device", default=None)
    ap.add_argument("--epochs", type=int, default=None, help="smoke tests only; PREREG is 24")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return train(args)


if __name__ == "__main__":
    sys.exit(main())
