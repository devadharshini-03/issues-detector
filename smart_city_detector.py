
"""
Smart City Road Problem Detector
--------------------------------
Demo detector for:
  - pothole (heuristic road-region detection)
  - water_overflow (blue/cyan region heuristic)
  - garbage and trash piles (YOLO-World prompts plus common COCO objects)
  - person, car, truck, bus, motorcycle, bicycle, bottle, cup, etc.

IMPORTANT:
This is a DEMO / prototype. Reliable pothole and drainage-overflow detection
requires a custom-trained YOLO model with labeled local images/videos.

Install:
    pip install -r requirements.txt

Run video:
    python smart_city_detector.py --source demo_city_problems.mp4

Run webcam:
    python smart_city_detector.py --source 0

Run webcam with a serial GPS receiver (replace COM3 with its port):
    python smart_city_detector.py --source 0 --gps-port COM3

Use Windows Location Services (enable location for desktop apps in Windows):
    python smart_city_detector.py --source 0 --windows-location

For a fixed camera, provide its coordinates:
    python smart_city_detector.py --source 0 --location 12.9716,77.5946

Put a newly generated GMAIL_APP_PASSWORD in the project .env file to enable email alerts.

Press Q to quit.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.message import EmailMessage
import os
from pathlib import Path
import sqlite3
import smtplib
import ssl
import subprocess
import threading
import time

import cv2
import numpy as np
from ultralytics import YOLO, YOLOWorld

GARBAGE_CLASSES = {
    "bottle", "cup", "banana", "apple", "backpack", "handbag",
    "suitcase", "umbrella", "sports ball", "book", "cell phone"
}

GARBAGE_WORLD_LABELS = [
    "garbage pile", "pile of garbage", "trash pile", "pile of trash",
    "rubbish heap", "waste heap", "waste dump", "landfill",
    "litter on the ground", "plastic waste", "garbage bags",
    "discarded furniture", "scrap heap",
]

ALERT_RECIPIENT = "smmethun2006@gmail.com"
ALERT_SENDER = "greenideatechlimited@gmail.com"


def parse_coordinates(value):
    """Parse a latitude,longitude pair supplied on the command line."""
    try:
        latitude, longitude = (float(part.strip()) for part in value.split(",", 1))
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("location must be LATITUDE,LONGITUDE")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise argparse.ArgumentTypeError("latitude/longitude are outside valid ranges")
    return latitude, longitude


def resolve_demo_kind(source, requested):
    """Choose a category filter, inferring demo clips from their filenames."""
    if requested != "auto":
        return requested

    source_name = Path(str(source)).stem.lower()
    if "pothole" in source_name:
        return "pothole"
    if any(term in source_name for term in ("garbage", "trash", "waste")):
        return "garbage"
    return "all"


def nmea_coordinate(value, hemisphere):
    """Convert an NMEA degrees/minutes coordinate to decimal degrees."""
    if not value:
        return None
    raw = float(value)
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    result = degrees + minutes / 60
    if hemisphere in ("S", "W"):
        result = -result
    return result


def parse_nmea_location(line):
    """Return coordinates from a valid NMEA RMC or GGA sentence, if present."""
    fields = line.split(",")
    if not fields:
        return None
    sentence_type = fields[0][-3:]
    try:
        if sentence_type == "RMC" and len(fields) > 6 and fields[2] == "A":
            latitude = nmea_coordinate(fields[3], fields[4])
            longitude = nmea_coordinate(fields[5], fields[6])
        elif sentence_type == "GGA" and len(fields) > 6 and int(fields[6] or "0") > 0:
            latitude = nmea_coordinate(fields[2], fields[3])
            longitude = nmea_coordinate(fields[4], fields[5])
        else:
            return None
        if latitude is None or longitude is None:
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        return latitude, longitude
    except (ValueError, IndexError):
        return None


class WindowsLocationProvider:
    """Poll Windows Location Services without triggering a permission prompt."""

    _POWERSHELL_QUERY = r"""
Add-Type -AssemblyName System.Device
$watcher = New-Object System.Device.Location.GeoCoordinateWatcher
try {
    if (-not $watcher.TryStart($true, [TimeSpan]::FromSeconds(8))) { exit 2 }
    $point = $watcher.Position.Location
    if ($point.IsUnknown) { exit 3 }
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $latitude = $point.Latitude.ToString("R", $culture)
    $longitude = $point.Longitude.ToString("R", $culture)
    $accuracy = $point.HorizontalAccuracy.ToString("R", $culture)
    [Console]::Out.WriteLine("$latitude,$longitude,$accuracy")
} finally {
    $watcher.Stop()
}
"""

    def __init__(self):
        if os.name != "nt":
            raise SystemExit("--windows-location is available only on Windows")
        self.latest = None
        self.accuracy_m = None
        self.last_fix_time = None
        self.last_error = "Waiting for a Windows location fix"
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()

    def _poll(self):
        while not self.stop_event.is_set():
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", self._POWERSHELL_QUERY],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    check=False,
                )
                if result.returncode == 0:
                    latitude_text, longitude_text, accuracy_text = result.stdout.strip().split(",", 2)
                    latitude, longitude = float(latitude_text), float(longitude_text)
                    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                        self.latest = (latitude, longitude)
                        accuracy = float(accuracy_text)
                        self.accuracy_m = accuracy if np.isfinite(accuracy) and accuracy >= 0 else None
                        self.last_fix_time = time.monotonic()
                        self.last_error = ""
                    else:
                        self.last_error = "Windows returned coordinates outside valid ranges"
                elif result.returncode == 2:
                    self.last_error = "Windows location access is disabled or unavailable"
                else:
                    self.last_error = "Windows has no current location fix"
            except subprocess.TimeoutExpired:
                self.last_error = "Windows location query timed out"
            except Exception as exc:
                self.last_error = f"Windows location query failed: {exc}"
            self.stop_event.wait(15)

    def current(self):
        if self.latest is not None and self.last_fix_time is not None:
            if time.monotonic() - self.last_fix_time <= 60:
                return self.latest, "Live Windows location", self.accuracy_m
        return None, self.last_error, None

    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1)


class LocationProvider:
    """Read fixed, Windows, or serial GPS location data."""

    def __init__(self, fixed_location=None, gps_port=None, gps_baudrate=9600,
                 windows_location=False):
        self.fixed_location = fixed_location
        self.serial = None
        self.windows_location = WindowsLocationProvider() if windows_location else None
        self.buffer = b""
        self.last_fix = None
        self.last_fix_time = None
        if gps_port:
            try:
                import serial
            except ImportError as exc:
                raise SystemExit("GPS support requires pyserial; install it with: pip install pyserial") from exc
            try:
                self.serial = serial.Serial(gps_port, gps_baudrate, timeout=0)
            except serial.SerialException as exc:
                raise SystemExit(f"Cannot open GPS serial port {gps_port}: {exc}") from exc

    def update(self):
        if self.serial is None:
            return
        try:
            available = self.serial.in_waiting
            data = self.serial.read(available or 1)
            if not data:
                return
            self.buffer += data
            lines = self.buffer.split(b"\n")
            self.buffer = lines.pop()
            for raw_line in lines:
                location = parse_nmea_location(raw_line.decode("ascii", errors="ignore").strip())
                if location:
                    self.last_fix = location
                    self.last_fix_time = time.monotonic()
        except Exception as exc:
            print(f"GPS read failed: {exc}")
            self.close()

    def current(self):
        if self.fixed_location is not None:
            return self.fixed_location, "Configured camera location", None
        if self.windows_location is not None:
            return self.windows_location.current()
        if self.last_fix is not None and self.last_fix_time is not None:
            if time.monotonic() - self.last_fix_time <= 60:
                return self.last_fix, "Live GPS location", None
        if self.serial is not None:
            return None, "Live GPS location unavailable (no recent GPS fix)", None
        return None, "Location unavailable (no GPS receiver or --location configured)", None

    def close(self):
        if self.windows_location is not None:
            self.windows_location.close()
        if self.serial is not None:
            try:
                self.serial.close()
            except Exception:
                pass
            self.serial = None


def capture_alert_image(frame, box, label, alert_dir, filename_prefix):
    """Save annotated full-frame evidence and a close-up crop."""
    image = frame.copy()
    x, y, width, height, confidence = box
    draw_box(image, (x, y, width, height), label, confidence, (30, 30, 220))
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise RuntimeError("Could not encode incident snapshot as JPEG")
    folder = Path(alert_dir)
    folder.mkdir(parents=True, exist_ok=True)
    filename = filename_prefix + "_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f") + ".jpg"
    image_path = folder / filename
    image_bytes = encoded.tobytes()
    image_path.write_bytes(image_bytes)

    frame_height, frame_width = frame.shape[:2]
    margin_x = max(12, int(width * 0.18))
    margin_y = max(12, int(height * 0.18))
    left = max(0, int(x) - margin_x)
    top = max(0, int(y) - margin_y)
    right = min(frame_width, int(x + width) + margin_x)
    bottom = min(frame_height, int(y + height) + margin_y)
    crop = frame[top:bottom, left:right].copy()
    if crop.size == 0:
        raise RuntimeError("Could not crop incident evidence")
    draw_box(crop, (x - left, y - top, width, height), label, confidence, (30, 30, 220))
    crop_path = image_path.with_name(image_path.stem + "_crop.jpg")
    crop_ok, crop_encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not crop_ok:
        raise RuntimeError("Could not encode incident crop as JPEG")
    crop_bytes = crop_encoded.tobytes()
    crop_path.write_bytes(crop_bytes)
    return image_path, image_bytes, crop_path, crop_bytes


def capture_pothole_image(frame, box, alert_dir):
    """Save an annotated still of the detected pothole."""
    return capture_alert_image(frame, box, "POTHOLE (DEMO)", alert_dir, "pothole")


def load_project_env():
    """Load the Gmail secret from the project .env without overriding process env."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return False
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        print("Could not read the project .env file; email will use process environment only.")
        return False

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or key.strip() != "GMAIL_APP_PASSWORD":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value and not os.environ.get("GMAIL_APP_PASSWORD"):
            os.environ["GMAIL_APP_PASSWORD"] = value
            return True
    return False


class AlertMailer:
    """Send incident evidence through Gmail using a private environment secret."""

    def __init__(self):
        loaded_from_file = load_project_env()
        self.password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
        self.enabled = bool(self.password)
        if not self.enabled:
            print("GMAIL_APP_PASSWORD is not set in .env or the environment; email is disabled.")
        elif loaded_from_file:
            print("Gmail alert email is enabled from the project .env file.")

    def send(self, attachments, location, accuracy_m, confidence, detected_at,
             incident_name, label, event_id):
        message = EmailMessage()
        message["Subject"] = f"Incident #{event_id}: {incident_name} - {detected_at.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        message["From"] = ALERT_SENDER
        message["To"] = ALERT_RECIPIENT

        if location[0] is not None:
            latitude, longitude = location[0]
            location_text = f"{location[1]}: {latitude:.6f}, {longitude:.6f}"
            if accuracy_m is not None:
                location_text += f" (reported accuracy: {accuracy_m:.0f} m)"
            maps_link = f"https://maps.google.com/?q={latitude:.6f},{longitude:.6f}"
            location_text += f"\nMap: {maps_link}"
        else:
            location_text = location[1]

        message.set_content(
            f"A {incident_name.lower()} was detected by the Smart City Road Problem Detector.\n\n"
            f"Detection time: {detected_at.astimezone().isoformat(timespec='seconds')}\n"
            f"Incident: #{event_id}\n"
            f"Detection label: {label}\n"
            f"Detection confidence: {confidence:.0%}\n"
            f"Location: {location_text}\n\n"
            "The attached image is the annotated detection frame. A close-up crop is saved with the local incident evidence. Please verify the report before taking action."
        )
        for image_path, image_bytes in attachments:
            message.add_attachment(image_bytes, maintype="image", subtype="jpeg", filename=image_path.name)

        self._send_smtp(message)
        print(f"Incident #{event_id} email sent to {ALERT_RECIPIENT}")

    def _send_smtp(self, message):
        """Try Gmail SSL first, then STARTTLS if the connection drops before submission."""
        context = ssl.create_default_context()
        for port in (465, 587):
            stage = "connect"
            try:
                if port == 465:
                    with smtplib.SMTP_SSL(
                        "smtp.gmail.com", port, context=context, timeout=25
                    ) as server:
                        stage = "authenticate"
                        server.login(ALERT_SENDER, self.password)
                        stage = "message submission"
                        server.send_message(message)
                else:
                    with smtplib.SMTP("smtp.gmail.com", port, timeout=25) as server:
                        server.ehlo()
                        stage = "STARTTLS handshake"
                        server.starttls(context=context)
                        server.ehlo()
                        stage = "authenticate"
                        server.login(ALERT_SENDER, self.password)
                        stage = "message submission"
                        server.send_message(message)
                if port == 587:
                    print("Gmail SMTP used STARTTLS on port 587.")
                return
            except smtplib.SMTPAuthenticationError:
                raise RuntimeError(
                    "Gmail rejected the App Password; check .env and use a newly generated password"
                ) from None
            except (OSError, ssl.SSLError, smtplib.SMTPServerDisconnected,
                    smtplib.SMTPConnectError) as exc:
                if port == 465 and stage != "message submission":
                    print("Gmail SMTP port 465 failed before message submission; trying STARTTLS on port 587.")
                    continue
                if stage == "authenticate":
                    raise RuntimeError(
                        "Gmail disconnected during SMTP authentication. Verify .env has a fresh App Password for "
                        "the sender account, 2-Step Verification is enabled, and the account allows App Passwords."
                    ) from None
                raise RuntimeError(
                    "SMTP port {} failed during {} ({})".format(port, stage, type(exc).__name__)
                ) from None
            except smtplib.SMTPException as exc:
                raise RuntimeError(
                    "SMTP port {} failed during {} ({})".format(port, stage, type(exc).__name__)
                ) from None


def send_alert_email(mailer, attachments, location, accuracy_m, confidence,
                     detected_at, incident_name, label, event_id):
    try:
        mailer.send(attachments, location, accuracy_m, confidence, detected_at,
                    incident_name, label, event_id)
    except Exception as exc:
        print(f"Could not send {incident_name.lower()} email for incident #{event_id}: {exc}")


class IncidentStore:
    """Small local SQLite incident log used by the detector and dashboard."""

    def __init__(self, database_path):
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detected_at TEXT NOT NULL,
                    issue_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    location_source TEXT NOT NULL,
                    accuracy_m REAL,
                    source TEXT NOT NULL,
                    snapshot_path TEXT NOT NULL,
                    crop_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new'
                        CHECK(status IN ('new', 'reviewed', 'resolved'))
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS incidents_detected_at_idx ON incidents(detected_at)")

    def add(self, detected_at, issue_type, label, confidence, location, accuracy_m,
            source, snapshot_path, crop_path):
        coordinates, location_source, _ = location
        latitude, longitude = coordinates if coordinates else (None, None)
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute("""
                INSERT INTO incidents (
                    detected_at, issue_type, label, confidence, latitude, longitude,
                    location_source, accuracy_m, source, snapshot_path, crop_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                detected_at.astimezone().isoformat(timespec="seconds"), issue_type,
                label, float(confidence), latitude, longitude, location_source,
                accuracy_m, str(source), Path(snapshot_path).name, Path(crop_path).name,
            ))
            return cursor.lastrowid


def box_iou(first, second):
    """Intersection over union for x, y, width, height boxes."""
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0, right - left) * max(0, bottom - top)
    union = max(1, aw * ah + bw * bh - intersection)
    return intersection / union


class TemporalIncidentTracker:
    """Require repeated detections and keep one alert per continuously visible object."""

    def __init__(self, min_hits=3, window=5, rearm_after=30, iou_threshold=0.25):
        self.min_hits = min_hits
        self.window = window
        self.rearm_after = rearm_after
        self.iou_threshold = iou_threshold
        self.frame_index = 0
        self.next_id = 1
        self.tracks = []

    def update(self, detections):
        self.frame_index += 1
        for track in self.tracks:
            track["missed"] += 1
            while track["hits"] and self.frame_index - track["hits"][0][0] >= self.window:
                track["hits"].pop(0)
        self.tracks = [track for track in self.tracks if track["missed"] < self.rearm_after]

        matched = set()
        confirmed = []
        for detection in sorted(detections, key=lambda item: item["confidence"], reverse=True):
            best_track = None
            best_iou = self.iou_threshold
            for track in self.tracks:
                if track["id"] in matched or track["kind"] != detection["kind"]:
                    continue
                overlap = box_iou(track["box"], detection["box"])
                if overlap >= best_iou:
                    best_iou, best_track = overlap, track
            if best_track is None:
                best_track = {
                    "id": self.next_id, "kind": detection["kind"],
                    "label": detection["label"], "box": detection["box"],
                    "confidence": detection["confidence"], "hits": [],
                    "missed": 0, "alerted": False,
                }
                self.next_id += 1
                self.tracks.append(best_track)
            best_track["box"] = detection["box"]
            best_track["label"] = detection["label"]
            best_track["confidence"] = max(best_track["confidence"], detection["confidence"])
            best_track["missed"] = 0
            matched.add(best_track["id"])
            best_track["hits"].append((self.frame_index, detection["confidence"]))
            if not best_track["alerted"] and len(best_track["hits"]) >= self.min_hits:
                best_track["alerted"] = True
                confirmed.append({
                    "kind": best_track["kind"], "label": best_track["label"],
                    "box": best_track["box"], "confidence": best_track["confidence"],
                })
        return confirmed


def map_issue_label(name):
    """Map custom model class names to supported incident categories."""
    normalized = name.lower().replace("-", "_").replace(" ", "_")
    if "pothole" in normalized:
        return "pothole", name
    if any(word in normalized for word in ("garbage", "trash", "rubbish", "litter", "waste", "landfill")):
        return "garbage", name
    if any(word in normalized for word in ("overflow", "flood", "water")):
        return "overflow", name
    return None, name

def find_largest_dark_road_region(frame):
    """Simple demo heuristic: finds dark/gray low-texture road-like regions."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Road-ish pixels: moderate brightness, low saturation.
    mask = cv2.inRange(hsv, np.array([0, 0, 35]), np.array([180, 85, 175]))
    h, w = mask.shape
    # Focus on lower 55% of frame where the road normally appears.
    mask[:int(h * 0.45), :] = 0

    kernel = np.ones((9, 9), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    if area < 0.04 * w * h:
        return None

    x, y, rw, rh = cv2.boundingRect(contour)
    return (x, y, rw, rh, contour)

def detect_pothole(frame):
    """
    Demo heuristic, NOT a production pothole model.
    Looks for dark irregular blobs inside the road area.
    """
    road = find_largest_dark_road_region(frame)
    if road is None:
        return None

    x, y, rw, rh, _ = road
    roi = frame[y:y+rh, x:x+rw]
    if roi.size == 0:
        return None

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Dark depressions / patches.
    dark = cv2.inRange(gray, 0, 72)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((11,11), np.uint8))

    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        a = cv2.contourArea(c)
        if a < 500 or a > 0.18 * rw * rh:
            continue
        px, py, pw, ph = cv2.boundingRect(c)
        if pw < 25 or ph < 15:
            continue
        candidates.append((a, px+x, py+y, pw, ph))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    a, px, py, pw, ph = candidates[0]
    confidence = min(0.96, 0.45 + a / max(1, rw*rh) * 4.0)
    return (px, py, pw, ph, confidence)

def detect_overflow(frame):
    """Demo water/drain overflow heuristic using blue/cyan water regions."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.array([82, 55, 45])
    upper = np.array([125, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)

    h, w = mask.shape
    mask[:int(h * 0.30), :] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5,5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15,15), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    c = max(contours, key=cv2.contourArea)
    a = cv2.contourArea(c)
    if a < 0.012 * w * h:
        return None

    x, y, rw, rh = cv2.boundingRect(c)
    confidence = min(0.98, 0.55 + a / (w*h) * 2.5)
    return (x, y, rw, rh, confidence)

def draw_box(frame, box, label, confidence, color):
    x, y, w, h = [int(v) for v in box]
    cv2.rectangle(frame, (x,y), (x+w,y+h), color, 2)
    text = f"{label} {confidence:.0%}"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.62, 2)
    yy = max(0, y-th-10)
    cv2.rectangle(frame, (x, yy), (x+tw+10, y), color, -1)
    cv2.putText(frame, text, (x+5, y-6), cv2.FONT_HERSHEY_SIMPLEX,
                0.62, (255,255,255), 2, cv2.LINE_AA)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="demo_city_problems.mp4",
                        help="Video path or webcam index, e.g. 0")
    parser.add_argument("--demo-kind", choices=("auto", "all", "pothole", "garbage"),
                        default="auto",
                        help="Incident category filter; auto infers pothole or garbage from the source filename")
    parser.add_argument("--output", default="detected_output.mp4")
    parser.add_argument("--conf", type=float, default=0.30)
    parser.add_argument("--garbage-conf", type=float, default=0.03,
                        help="YOLO-World threshold; tune against labeled validation clips")
    parser.add_argument("--garbage-model", default="yolov8s-worldv2.pt",
                        help="YOLO-World weights used to find trash piles and litter")
    parser.add_argument("--issue-model", default=None,
                        help="Optional custom YOLO model trained for pothole/garbage/overflow classes")
    parser.add_argument("--world-fallback", action="store_true",
                        help="Also run YOLO-World when --issue-model is supplied")
    parser.add_argument("--enable-demo-heuristics", action="store_true",
                        help="Enable unreliable visual pothole/overflow heuristics; can produce false alerts")
    parser.add_argument("--alert-dir", default="alerts",
                        help="Folder where incident snapshots are saved")
    parser.add_argument("--database", default="reports/incidents.sqlite3",
                        help="SQLite file used for the incident history")
    parser.add_argument("--confirm-hits", type=int, default=3,
                        help="Require this many detections before creating an incident")
    parser.add_argument("--confirm-window", type=int, default=5,
                        help="Count confirmation hits within this many frames")
    parser.add_argument("--rearm-after", type=int, default=30,
                        help="Re-arm an object after this many missed frames")
    location_group = parser.add_mutually_exclusive_group()
    location_group.add_argument("--gps-port",
                                help="Serial port for a NMEA GPS receiver, e.g. COM3")
    location_group.add_argument("--location", type=parse_coordinates,
                                help="Fixed camera location as LATITUDE,LONGITUDE")
    location_group.add_argument("--windows-location", action="store_true",
                                help="Read the current location from Windows Location Services")
    parser.add_argument("--gps-baudrate", type=int, default=9600,
                        help="Serial GPS baud rate (default: 9600)")
    args = parser.parse_args()
    if args.confirm_hits < 1 or args.confirm_window < args.confirm_hits or args.rearm_after < 1:
        parser.error("confirmation hits/window and rearm frames must be positive; window must be >= hits")

    demo_kind = resolve_demo_kind(args.source, args.demo_kind)
    run_garbage_detection = demo_kind in ("all", "garbage")
    demo_heuristics_enabled = args.enable_demo_heuristics or (
        demo_kind == "pothole" and args.issue_model is None
    )

    location_provider = LocationProvider(
        fixed_location=args.location,
        gps_port=args.gps_port,
        gps_baudrate=args.gps_baudrate,
        windows_location=args.windows_location,
    )
    mailer = AlertMailer()
    email_executor = ThreadPoolExecutor(max_workers=1) if mailer.enabled else None
    incident_store = IncidentStore(args.database)
    tracker = TemporalIncidentTracker(
        min_hits=args.confirm_hits,
        window=args.confirm_window,
        rearm_after=args.rearm_after,
    )

    issue_model = YOLO(args.issue_model) if args.issue_model else None
    model = YOLO("yolo26n.pt") if issue_model is None and run_garbage_detection else None
    garbage_model = None
    if run_garbage_detection and (issue_model is None or args.world_fallback):
        garbage_model = YOLOWorld(args.garbage_model)
        garbage_model.set_classes(GARBAGE_WORLD_LABELS)
    print(f"Incident category filter: {demo_kind}")
    if issue_model:
        print(f"Using custom issue model: {args.issue_model}")
    if run_garbage_detection and issue_model is None:
        print("Using YOLO-World garbage detection.")
    if demo_kind == "pothole" and issue_model is None:
        print("Using the experimental pothole demo heuristic; verify detections before acting on alerts.")
    elif demo_kind == "all" and demo_heuristics_enabled:
        print("WARNING: demo pothole/overflow heuristics are enabled and can create false incidents.")
    elif demo_kind == "all":
        print("Pothole/overflow demo heuristics are disabled; supply --issue-model for trained detection.")
    if run_garbage_detection:
        print("For production accuracy, label local footage and train a custom model with train_detector.py.")

    source = int(args.source) if str(args.source).isdigit() else args.source
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open source: {args.source}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)

    writer = None
    if not str(args.source).isdigit():
        writer = cv2.VideoWriter(
            args.output,
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps, (width, height)
        )

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            original_frame = frame.copy()
            location_provider.update()
            detections = []
            coco_candidates = []
            if issue_model is not None:
                result = issue_model.predict(original_frame, conf=args.conf, verbose=False)[0]
                if result.boxes is not None:
                    for box_result in result.boxes:
                        class_id = int(box_result.cls[0])
                        confidence = float(box_result.conf[0])
                        label = result.names[class_id]
                        kind, label = map_issue_label(label)
                        if kind is None or (demo_kind != "all" and kind != demo_kind):
                            continue
                        x1, y1, x2, y2 = box_result.xyxy[0].cpu().numpy().astype(int)
                        box = (int(x1), int(y1), int(x2 - x1), int(y2 - y1))
                        detections.append({"kind": kind, "label": label, "box": box,
                                           "confidence": confidence})
                        draw_box(frame, box, kind.upper() + ": " + label, confidence,
                                 (30, 30, 220) if kind == "pothole" else (40, 180, 40))
            else:
                if demo_heuristics_enabled and demo_kind in ("all", "pothole"):
                    pothole = detect_pothole(original_frame)
                    if pothole:
                        x, y, box_width, box_height, confidence = pothole
                        box = (x, y, box_width, box_height)
                        detections.append({"kind": "pothole", "label": "demo heuristic",
                                           "box": box, "confidence": confidence})
                        draw_box(frame, box, "POTHOLE (DEMO)", confidence, (30, 30, 220))

                if demo_heuristics_enabled and demo_kind == "all":
                    overflow = detect_overflow(original_frame)
                    if overflow:
                        x, y, box_width, box_height, confidence = overflow
                        box = (x, y, box_width, box_height)
                        detections.append({"kind": "overflow", "label": "blue water region (demo)",
                                           "box": box, "confidence": confidence})
                        draw_box(frame, box, "WATER OVERFLOW (DEMO)", confidence, (220, 120, 30))

                if run_garbage_detection:
                    result = model.predict(original_frame, conf=args.conf, verbose=False)[0]
                    if result.boxes is not None:
                        for box_result in result.boxes:
                            class_id = int(box_result.cls[0])
                            name = result.names[class_id]
                            if name not in GARBAGE_CLASSES:
                                continue
                            confidence = float(box_result.conf[0])
                            x1, y1, x2, y2 = box_result.xyxy[0].cpu().numpy().astype(int)
                            coco_candidates.append({"kind": "garbage", "label": name,
                                                    "box": (int(x1), int(y1), int(x2-x1), int(y2-y1)),
                                                    "confidence": confidence, "priority": 1})

            if run_garbage_detection and garbage_model is not None:
                world_result = garbage_model.predict(
                    original_frame, conf=args.garbage_conf, verbose=False
                )[0]
                image_area = original_frame.shape[0] * original_frame.shape[1]
                world_candidates = []
                if world_result.boxes is not None:
                    for box_result in world_result.boxes:
                        class_id = int(box_result.cls[0])
                        name = world_result.names[class_id]
                        confidence = float(box_result.conf[0])
                        x1, y1, x2, y2 = box_result.xyxy[0].cpu().numpy().astype(int)
                        box = (int(x1), int(y1), int(x2-x1), int(y2-y1))
                        area = max(0, box[2]) * max(0, box[3])
                        if any(term in name.lower() for term in ("pile", "heap", "dump", "landfill")) and area < 0.01 * image_area:
                            continue
                        world_candidates.append({"kind": "garbage", "label": name,
                                                 "box": box, "confidence": confidence,
                                                 "priority": 0})

                # Suppress overlapping boxes from different prompts/models, keeping broad scene boxes.
                candidates = sorted(coco_candidates + world_candidates,
                                    key=lambda item: (item["priority"], -item["confidence"]))
                accepted = []
                for candidate in candidates:
                    if any(box_iou(candidate["box"], previous["box"]) >= 0.55
                           for previous in accepted):
                        continue
                    accepted.append(candidate)
                for candidate in accepted:
                    detections.append({key: candidate[key]
                                       for key in ("kind", "label", "box", "confidence")})
                    draw_box(frame, candidate["box"], "GARBAGE: " + candidate["label"],
                             candidate["confidence"], (40, 180, 40))

            if demo_kind != "all":
                detections = [detection for detection in detections
                              if detection["kind"] == demo_kind]
            current_kinds = sorted({detection["kind"] for detection in detections})
            confirmed_events = tracker.update(detections)
            for event in confirmed_events:
                detected_at = datetime.now().astimezone()
                location, location_source, accuracy_m = location_provider.current()
                event_location = (location, location_source, accuracy_m)
                prefix = {"pothole": "pothole", "garbage": "garbage", "overflow": "overflow"}[event["kind"]]
                label = event["label"]
                issue_name = {"pothole": "Pothole", "garbage": "Garbage", "overflow": "Water overflow"}[event["kind"]]
                try:
                    image_path, image_bytes, crop_path, crop_bytes = capture_alert_image(
                        original_frame, (*event["box"], event["confidence"]),
                        issue_name + ": " + label, args.alert_dir, prefix,
                    )
                    incident_id = incident_store.add(
                        detected_at, event["kind"], label, event["confidence"],
                        event_location, accuracy_m, args.source, image_path, crop_path,
                    )
                    print(f"Incident #{incident_id} recorded: {issue_name} ({label}); evidence: {image_path}")
                    if email_executor:
                        email_executor.submit(
                            send_alert_email, mailer,
                            [(image_path, image_bytes)],
                            event_location, accuracy_m, event["confidence"],
                            detected_at, issue_name, label, incident_id,
                        )
                except Exception as exc:
                    print(f"Could not save {issue_name.lower()} incident: {exc}")

            # Compact live status; alerts are emitted only after temporal confirmation.
            cv2.rectangle(frame, (15, 15), (570, 142), (20, 20, 20), -1)
            cv2.putText(frame, "SMART CITY ROAD MONITOR", (30, 43),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
            status_text = " | ".join(kind.upper() for kind in current_kinds) if current_kinds else "NO CANDIDATES"
            cv2.putText(frame, status_text[:58], (30, 76),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
            if current_kinds:
                cv2.putText(frame, f"Alert rule: {args.confirm_hits} hits / {args.confirm_window} frames",
                            (30, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (210, 210, 210), 1)
            cv2.putText(frame, "Q = quit | history: reports/incidents.sqlite3", (30, 128),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.43, (210, 210, 210), 1)

            if writer:
                writer.write(frame)

            cv2.imshow("Smart City Detector", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        if writer:
            writer.release()
        location_provider.close()
        cv2.destroyAllWindows()
        if email_executor:
            email_executor.shutdown(wait=True)

    print(f"Finished. Output: {args.output}" if writer else "Finished.")

if __name__ == "__main__":
    main()
