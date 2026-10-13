"""Train a custom Ultralytics detector for local road issue classes."""

import argparse
from pathlib import Path

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser(
        description="Train a custom smart-city issue detector from a labeled YOLO dataset"
    )
    parser.add_argument("--data", required=True, help="Path to the dataset YAML file")
    parser.add_argument("--model", default="yolo26n.pt", help="Starting checkpoint")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None, help="Examples: cpu, 0, 0,1")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--project", default="runs/smart-city")
    parser.add_argument("--name", default="city-issues")
    args = parser.parse_args()

    if args.epochs < 1 or args.imgsz < 32 or args.batch < 1:
        parser.error("epochs, image size, and batch must be positive")

    model = YOLO(args.model)
    results = model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        patience=args.patience,
        seed=args.seed,
        project=args.project,
        name=args.name,
        plots=True,
        save=True,
    )
    save_dir = Path(results.save_dir)
    best_weights = save_dir / "weights" / "best.pt"
    print("Training run saved to: {}".format(save_dir))
    print("Best model: {}".format(best_weights))
    print("Use it with: python smart_city_detector.py --source VIDEO.mp4 --issue-model \"{}\"".format(best_weights))


if __name__ == "__main__":
    main()
