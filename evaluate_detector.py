"""Evaluate a trained detector on a held-out YOLO validation or test split."""

import argparse
import json
from pathlib import Path

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser(
        description="Report precision, recall, and mAP on a labeled held-out split"
    )
    parser.add_argument("--model", required=True, help="Trained weights, usually best.pt")
    parser.add_argument("--data", required=True, help="Dataset YAML path")
    parser.add_argument("--split", choices=("val", "test"), default="test")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", default="reports/evaluation.json")
    args = parser.parse_args()

    model = YOLO(args.model)
    metrics = model.val(
        data=args.data,
        split=args.split,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        plots=True,
    )
    report = {
        "model": str(Path(args.model)),
        "dataset": str(Path(args.data)),
        "split": args.split,
        "metrics": {},
    }
    for name, value in metrics.results_dict.items():
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, (int, float)):
            report["metrics"][name] = float(value)
        else:
            report["metrics"][name] = value

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Evaluation split: {}".format(args.split))
    for name, value in report["metrics"].items():
        print("{}: {}".format(name, value))
    print("Metric report saved to: {}".format(output_path))
    print("Review the confusion matrix and prediction plots saved by Ultralytics too.")


if __name__ == "__main__":
    main()
