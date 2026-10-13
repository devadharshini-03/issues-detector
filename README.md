# Smart City Issues Detector

A Python-based video monitoring tool for detecting potholes, garbage/trash, and water-overflow incidents in video footage or a webcam feed.

The detector saves image evidence, records incidents in SQLite, can include location data, optionally sends Gmail alerts, and includes a local dashboard for reviewing and resolving reports.

## Main features

- YOLO and YOLO-World detection for garbage and custom road-issue models
- Optional demo heuristics for potholes and water overflow
- Multi-frame confirmation and repeat-alert suppression
- Annotated video output plus full-frame and cropped evidence images
- SQLite incident history with `new`, `reviewed`, and `resolved` statuses
- Optional fixed, GPS, or Windows location data
- Optional Gmail incident alerts and report summaries
- Local review dashboard

## Quick start

```powershell
pip install -r requirements.txt
python smart_city_detector.py --source garbage1.mp4
python incident_dashboard.py
```

Open `http://127.0.0.1:8765/` to review saved incidents.

## Custom training

Prepare a labeled YOLO dataset, copy `dataset.yaml.example` to `dataset.yaml`, then run:

```powershell
python train_detector.py --data dataset.yaml --epochs 100 --imgsz 640
python evaluate_detector.py --model "runs/smart-city/city-issues/weights/best.pt" --data dataset.yaml --split test
```

See [ADVANCED_SETUP.md](ADVANCED_SETUP.md) for detector options, data preparation, alerting, location support, and evaluation guidance.

## Security

Keep `GMAIL_APP_PASSWORD` only in the private `.env` file. The file is intentionally ignored by Git.
