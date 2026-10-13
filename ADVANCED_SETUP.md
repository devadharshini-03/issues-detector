# Advanced smart city detector

## What the detector now records

- An object must appear repeatedly (default: 3 detections within 5 frames) before it becomes an incident.
- Overlapping trash detections are combined, and one continuously visible object creates one incident. It can alert again after it has been absent for 30 frames.
- Each confirmed incident saves an annotated full frame and a close-up crop under alerts/.
- Incident details are stored in reports/incidents.sqlite3: category, label, score, time, video or camera source, location, reported location accuracy when Windows provides it, evidence filenames, and review status.
- If GMAIL_APP_PASSWORD is set in the process environment, each email includes the annotated detection frame, a map link when coordinates are available, and the incident number. The close-up crop remains in local evidence. The password is not stored in the source code or database.

By default, source filenames containing `pothole` select the pothole demo and suppress garbage detections; filenames containing `garbage`, `trash`, or `waste` select garbage detection and suppress pothole detections. Other sources run all available detections. Pothole demo sources use the experimental pothole heuristic, which can mistake gravel, shadows, or trash for incidents. For generic or mixed footage, pothole/overflow heuristics remain off unless `--enable-demo-heuristics` is supplied. Use `--demo-kind all` to run every available detector, or `--demo-kind pothole` / `--demo-kind garbage` to select a category explicitly. A custom model trained on local labeled footage is needed for dependable pothole/overflow detection.

Run the included demo clips:

~~~powershell
python smart_city_detector.py --source pothole.mp4
python smart_city_detector.py --source garbage1.mp4
~~~

## Label footage and train a custom model

Create a dataset with this layout:

~~~text
datasets/smart_city/
  images/
    train/
    val/
    test/
  labels/
    train/
    val/
    test/
~~~

Each image needs a matching YOLO label text file with the same base filename. Each row is class_id x_center y_center width height, using normalized values from 0 to 1. The included dataset.yaml.example maps class 0 to pothole, 1 to garbage, and 2 to water_overflow. Copy it to dataset.yaml and set the dataset path.

Label varied local examples: clean roads, small and large potholes, roadside trash piles, ordinary bins, and water that is not an overflow. Use separate recordings for train, validation, and test; neighboring frames from one video should not be split across all three sets. Review the validation and test predictions and adjust labels and data before relying on email alerts.

Train:

~~~powershell
python train_detector.py --data dataset.yaml --epochs 100 --imgsz 640
~~~

Run the resulting best model:

~~~powershell
python smart_city_detector.py --source garbage1.mp4 --issue-model "runs/smart-city/city-issues/weights/best.pt"
~~~

The trainer prints the actual best-model path at the end. For a custom issue model, class names containing pothole, garbage/trash/waste, or water/overflow/flood are mapped into the incident categories. To also run the open-vocabulary garbage detector alongside the custom model, add --world-fallback.

Evaluate the best checkpoint on footage reserved for testing:

~~~powershell
python evaluate_detector.py --model "runs/smart-city/city-issues/weights/best.pt" --data dataset.yaml --split test
~~~

This writes aggregate precision, recall, and mAP values to reports/evaluation.json and asks Ultralytics to save plots such as the confusion matrix. Compare reports from different training runs on the same held-out split. Also replay complete clips and count false incident emails and missed issues; frame-level metrics do not measure alert quality or repeat suppression by themselves.

## Review incident history

Start the detector first so it creates its database, then open another terminal:

~~~powershell
python incident_dashboard.py
~~~

Open http://127.0.0.1:8765/. The dashboard shows snapshots, crops, time, source, confidence, location/map links, and lets you mark incidents new, reviewed, or resolved. It binds to localhost by default.

To email one summary containing saved incident details and evidence images, run:

~~~powershell
python send_incident_report.py --database reports/garbage1_run.sqlite3 --media-dir alerts/garbage1_run
~~~

The report sender reads the same private .env value and sends one message to the configured recipient.
To resend one saved incident after confirming it was not already delivered, add --incident-id ID and point the database/media arguments at that run.

## Location and email

Use --windows-location, --gps-port COM3, or a fixed --location LATITUDE,LONGITUDE. Location access must be enabled by the device owner. Fixed camera coordinates have no measured accuracy; the dashboard labels accuracy only when the provider reports it.

Create a new Gmail App Password after revoking any value previously shared in chat. Put the replacement in the project .env file as GMAIL_APP_PASSWORD=your-new-app-password. The detector loads it automatically; a process environment variable takes precedence. The .env file is ignored by Git. Without a configured password, incidents and evidence are still saved locally and no email is sent.

## Useful detector options

~~~text
--confirm-hits 3       Detections required before an incident
--confirm-window 5     Frame window for those detections
--rearm-after 30       Missed frames before a tracked issue can alert again
--database PATH        Incident SQLite database path
--alert-dir PATH       Evidence image directory
--conf 0.30            Custom model confidence threshold
--garbage-conf 0.03    YOLO-World threshold when that model is active
--demo-kind auto       Select a category from the source filename (default)
--enable-demo-heuristics  Opt in to unreliable pothole/overflow heuristics for mixed footage
~~~

Tune thresholds using a held-out set of clips and record false alerts and missed issues. YOLO-World's score is not calibrated as a probability; very low values should not be treated as proof that an image contains garbage.
