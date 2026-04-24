#!/usr/bin/env python3
"""No-servo RGB/thermal alignment calibrator for the Jetson Nano.

This standalone test app is intentionally separate from the main prototype.
It keeps the RGB camera, MLX90640 thermal camera, detector, and sensor sidebar,
but it never initialises or commands the pan/tilt servos.  Use it to tune
RGB-to-thermal alignment without nudging the fragile camera/sensor rig.

Included:
- RGB camera head/face detection
- thermal region extraction from the aligned face position
- guided multi-point RGB/thermal calibration
- least-squares RGB + distance alignment model fitting
- URM13 distance integration
- BME280 ambient temperature/humidity/pressure integration
- simple correction model against a configurable "normal" face heat proxy
"""

import argparse
import builtins
import collections
import json
import os
import threading
import time

import cv2
import numpy as np
import smbus2

# DNN SSD face detector — lightweight, fast, no TF needed.
DNN_AVAILABLE = False
_dnn_net = None
_DNN_PROTO = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "deploy.prototxt")
_DNN_MODEL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets",
    "res10_300x300_ssd_iter_140000.caffemodel")

# Work around older Python 3.6 + CircuitPython typing issues on the Nano.
builtins.ReadableBuffer = bytes
builtins.WriteableBuffer = bytearray

try:
    import adafruit_dht
except Exception:
    adafruit_dht = None

import adafruit_mlx90640
import board
import busio


# Camera and overlay
WINDOW_NAME = "Thermal RGB Alignment Calibrator"
MAIN_CAMERA_ROTATE_DEGREES = 90
THERMAL_ROTATE_DEGREES = 180
MAIN_CAMERA_FLIP_HORIZONTAL = False
THERMAL_INSET_SIZE = (192, 144)
THERMAL_INSET_MARGIN = 16
SIDEBAR_WIDTH = 300
SIDEBAR_BG = (30, 30, 30)       # dark grey background
SIDEBAR_FONT = cv2.FONT_HERSHEY_SIMPLEX
SIDEBAR_FONT_SCALE = 0.48
SIDEBAR_LINE_HEIGHT = 20
SIDEBAR_MARGIN_X = 10
SIDEBAR_MARGIN_Y = 20

GST_PIPELINE = (
    "nvarguscamerasrc ! "
    "video/x-raw(memory:NVMM),width=640,height=480,framerate=30/1 ! "
    "nvvidconv ! video/x-raw,format=BGRx ! "
    "videoconvert ! video/x-raw,format=BGR ! appsink drop=1"
)

# Sensors
URM13_ADDR = 0x12
MLX_ADDR = 0x33
URM13_MIN_RELIABLE_CM = 40
URM13_MAX_RELIABLE_CM = 1200
URM13_MEDIAN_WINDOW = 5
BME280_ADDR = 0x76
BME280_READ_INTERVAL_S = 2.0  # I2C reads are fast and reliable, no retries needed
DHT_PIN = board.D6  # legacy DHT22 fallback (board pin 31)

# Servo / tracking
SERVO_FREQ = 50
PERIOD_NS = int(1e9 / SERVO_FREQ)
MIN_PULSE_US = 500
MAX_PULSE_US = 2500
CENTER_ANGLE = 90
DEFAULT_PAN_ANGLE = 160
DEFAULT_TILT_ANGLE = 90
SERVO_CHIP = "/sys/class/pwm/pwmchip0"
PAN_PWM = "/sys/class/pwm/pwmchip0/pwm2"   # board pin 33
TILT_PWM = "/sys/class/pwm/pwmchip0/pwm0"  # board pin 32
MANUAL_STEP_DEG = 3
HORIZONTAL_TRACK_SIGN = -1
VERTICAL_TRACK_SIGN = 1
TRACK_UPDATE_S = 0.10
TRACK_DEADBAND_MIN_X = 35
TRACK_DEADBAND_MIN_Y = 25
TRACK_DEADBAND_FACE_RATIO = 0.4  # deadband = face_size * this ratio (bigger face = bigger deadband)
TRACK_MAX_STEP = 4
TRACK_GAIN = 0.5           # proportional gain — lower = gentler, less overshoot
SERVO_RELAX_DELAY = 0.5    # seconds of no correction before servo PWM is disabled (prevents weight-induced wobble)
DETECTION_JUMP_THRESHOLD = 150  # max pixels a face centre can jump between frames before being treated as an outlier

# Detection
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(SCRIPT_DIR, "assets")
FRONTAL_CASCADE = os.path.join(ASSET_DIR, "haarcascade_frontalface_default.xml")
PROFILE_CASCADE = os.path.join(ASSET_DIR, "haarcascade_profileface.xml")
DETECT_SCALE = 0.5
FACE_MIN_SIZE = 60
DNN_CONFIDENCE_THRESHOLD = 0.6  # reject low-confidence DNN detections

# Alignment and correction heuristics
ALIGNMENT_FILE = os.path.expanduser("~/Desktop/thermal_rgb_alignment.json")
GUIDED_ALIGNMENT_FILE = os.path.expanduser("~/Desktop/thermal_rgb_guided_alignment.json")
THERMAL_SCALE_X = 1.0
THERMAL_SCALE_Y = 1.0
THERMAL_OFFSET_X = 0.0
THERMAL_OFFSET_Y = 0.0
ALIGNMENT_NUDGE_PX = 1.0
ALIGNMENT_SCALE_STEP = 0.02
ALIGNMENT_HOTSPOT_RADIUS = 6
ALIGNMENT_SAMPLE_MERGE_CM = 8
GUIDED_DISTANCES_CM = [50, 80, 110]
GUIDED_TARGETS = [
    (0.25, 0.25), (0.50, 0.25), (0.75, 0.25),
    (0.25, 0.50), (0.50, 0.50), (0.75, 0.50),
    (0.25, 0.75), (0.50, 0.75), (0.75, 0.75),
]
TARGET_TOLERANCE_PX = 55
DISTANCE_TOLERANCE_CM = 12
MIN_FIT_SAMPLES = 9
THERMAL_BORDER_IGNORE_PX = 2
THERMAL_LOCAL_SEARCH_RADIUS = 8
THERMAL_PERCENTILE = 85
THERMAL_BLOB_PERCENTILE = 88
THERMAL_MIN_BLOB_PIXELS = 3
REFERENCE_DISTANCE_CM = 100.0
REFERENCE_AMBIENT_C = 21.0
REFERENCE_HUMIDITY = 50.0
NORMAL_FACE_PROXY_C = 34.0
DISTANCE_COEFF_PER_M = 0.35
AMBIENT_COEFF = 0.12
HUMIDITY_COEFF = 0.01
NORMAL_PROXY_TOLERANCE_C = 0.8


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = True

        self.distance_cm = None
        self.distance_status = "waiting"
        self.distance_error = None

        self.thermal = None
        self.thermal_error = None

        # Detection runs in its own thread so it doesn't block display.
        self.detect_frame = None       # latest frame for the detector to process
        self.detect_frame_id = 0       # incremented each time a new frame is posted
        self.detections = []           # latest detection results
        self.detect_fps = 0.0          # detection-thread FPS

        self.ambient_temp_c = None
        self.ambient_humidity = None
        self.ambient_pressure_hpa = None
        self.ambient_source = None       # "BME280" or "DHT22"
        self.ambient_error = "not started"
        self.ambient_updated_at = None


class HardwarePWMServo:
    def __init__(self, pwm_path, chip_path, pwm_id):
        self.pwm_path = pwm_path
        self.active = False
        self.relaxed = False
        self.last_angle = CENTER_ANGLE
        self.error = None
        try:
            if not os.path.exists(pwm_path):
                try:
                    with open(os.path.join(chip_path, "export"), "w") as fh:
                        fh.write(str(pwm_id))
                except OSError:
                    pass
                time.sleep(0.1)
            if not os.path.exists(pwm_path):
                raise RuntimeError("PWM path not available: {}".format(pwm_path))
            self._write("period", str(PERIOD_NS))
            self._write("enable", "1")
            self.active = True
            self.set_angle(CENTER_ANGLE)
        except Exception as exc:
            self.error = str(exc)
            self.active = False

    def _write(self, attr, value):
        with open(os.path.join(self.pwm_path, attr), "w") as fh:
            fh.write(value)

    def set_angle(self, angle):
        if not self.active:
            return
        if self.relaxed:
            self.wake()
        angle = max(0, min(180, int(angle)))
        pulse_us = MIN_PULSE_US + (angle / 180.0) * (MAX_PULSE_US - MIN_PULSE_US)
        duty_ns = int(pulse_us * 1000)
        self._write("duty_cycle", str(duty_ns))
        self.last_angle = angle

    def relax(self):
        """Disable PWM output so the servo stops holding position.

        The servo will sit at its last mechanical position under gravity
        without actively fighting.  Prevents weight-induced oscillation
        on rigs where the camera assembly is too heavy for the servo to
        hold perfectly still.
        """
        if not self.active or self.relaxed:
            return
        try:
            self._write("enable", "0")
            self.relaxed = True
        except Exception:
            pass

    def wake(self):
        """Re-enable PWM output so the servo can move again.

        IMPORTANT: duty_cycle is written BEFORE enable to prevent a
        glitch where the servo briefly sees a stale/zero duty value
        and jumps to a random position before the correct pulse is set.
        """
        if not self.active or not self.relaxed:
            return
        try:
            # Set duty cycle to the correct position FIRST (while still disabled)
            if self.last_angle is not None:
                angle = max(0, min(180, int(self.last_angle)))
                pulse_us = MIN_PULSE_US + (angle / 180.0) * (MAX_PULSE_US - MIN_PULSE_US)
                duty_ns = int(pulse_us * 1000)
                self._write("duty_cycle", str(duty_ns))
            # THEN enable — servo starts at the correct position immediately
            self._write("enable", "1")
            self.relaxed = False
        except Exception:
            pass

    def stop(self):
        if not self.active:
            return
        try:
            self._write("enable", "0")
        except Exception:
            pass


def clamp_angle(angle):
    return max(0.0, min(180.0, float(angle)))


def rotate_frame(frame, rotation):
    rotation_map = {
        None: None,
        0: None,
        90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180,
        270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    rotate_code = rotation_map.get(rotation)
    if rotate_code is None:
        return frame
    return cv2.rotate(frame, rotate_code)


def transform_main_camera_frame(frame):
    frame = rotate_frame(frame, MAIN_CAMERA_ROTATE_DEGREES)
    if MAIN_CAMERA_FLIP_HORIZONTAL:
        frame = cv2.flip(frame, 1)
    return frame


def default_alignment():
    return {
        "scale_x": THERMAL_SCALE_X,
        "scale_y": THERMAL_SCALE_Y,
        "offset_x": THERMAL_OFFSET_X,
        "offset_y": THERMAL_OFFSET_Y,
        "distance_samples": [],
    }


def load_alignment(path=ALIGNMENT_FILE):
    alignment = default_alignment()
    try:
        with open(path, "r") as fh:
            saved = json.load(fh)
        for key in alignment:
            if key == "distance_samples":
                continue
            if key in saved:
                alignment[key] = float(saved[key])
        samples = []
        for item in saved.get("distance_samples", []):
            try:
                samples.append({
                    "distance_cm": float(item["distance_cm"]),
                    "offset_x": float(item["offset_x"]),
                    "offset_y": float(item["offset_y"]),
                })
            except Exception:
                pass
        alignment["distance_samples"] = sorted(
            samples, key=lambda item: item["distance_cm"])
        return alignment, None
    except IOError:
        return alignment, "no saved alignment"
    except Exception as exc:
        return alignment, "alignment load failed: {}".format(exc)


def save_alignment(alignment, path=ALIGNMENT_FILE):
    payload = dict(alignment)
    payload["distance_samples"] = sorted(
        payload.get("distance_samples", []),
        key=lambda item: item["distance_cm"])
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def make_guided_steps():
    steps = []
    for distance_cm in GUIDED_DISTANCES_CM:
        for target_x, target_y in GUIDED_TARGETS:
            steps.append({
                "distance_cm": float(distance_cm),
                "target_x": float(target_x),
                "target_y": float(target_y),
            })
    return steps


def default_guided_model():
    return {
        "version": 1,
        "samples": [],
        "coeff_x": None,
        "coeff_y": None,
        "rmse_px": None,
        "mean_error_px": None,
        "max_error_px": None,
        "worst_sample": None,
        "updated_at": None,
    }


def load_guided_model(path=GUIDED_ALIGNMENT_FILE):
    model = default_guided_model()
    try:
        with open(path, "r") as fh:
            saved = json.load(fh)
        if isinstance(saved.get("samples"), list):
            model["samples"] = saved["samples"]
        for key in ("coeff_x", "coeff_y"):
            if isinstance(saved.get(key), list):
                model[key] = [float(v) for v in saved[key]]
        for key in ("rmse_px", "mean_error_px", "max_error_px"):
            if saved.get(key) is not None:
                model[key] = float(saved[key])
        model["worst_sample"] = saved.get("worst_sample")
        model["updated_at"] = saved.get("updated_at")
        return model, None
    except IOError:
        return model, "no saved guided model"
    except Exception as exc:
        return model, "guided model load failed: {}".format(exc)


def save_guided_model(model, path=GUIDED_ALIGNMENT_FILE):
    payload = dict(model)
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def guided_features(rgb_x, rgb_y, distance_cm, rgb_w, rgb_h):
    x = float(rgb_x) / max(1.0, float(rgb_w))
    y = float(rgb_y) / max(1.0, float(rgb_h))
    z = 1.0 / max(1.0, float(distance_cm or REFERENCE_DISTANCE_CM))
    return np.array([1.0, x, y, z, x * z, y * z, x * y, x * x, y * y], dtype=np.float64)


def fit_guided_model(model, rgb_w, rgb_h):
    samples = [
        (idx, item) for idx, item in enumerate(model.get("samples", []))
        if item.get("distance_cm") is not None and item.get("enabled", True)
    ]
    if len(samples) < MIN_FIT_SAMPLES:
        return False, "need {} samples".format(MIN_FIT_SAMPLES)

    design = np.vstack([
        guided_features(
            item["rgb_x"], item["rgb_y"], item["distance_cm"], rgb_w, rgb_h)
        for _, item in samples
    ])
    target_x = np.array([item["thermal_x"] for _, item in samples], dtype=np.float64)
    target_y = np.array([item["thermal_y"] for _, item in samples], dtype=np.float64)
    coeff_x, _, _, _ = np.linalg.lstsq(design, target_x, rcond=None)
    coeff_y, _, _, _ = np.linalg.lstsq(design, target_y, rcond=None)
    pred_x = design.dot(coeff_x)
    pred_y = design.dot(coeff_y)
    errors = np.sqrt((pred_x - target_x) ** 2 + (pred_y - target_y) ** 2)

    model["coeff_x"] = [float(v) for v in coeff_x]
    model["coeff_y"] = [float(v) for v in coeff_y]
    model["rmse_px"] = float(np.sqrt(np.mean(errors ** 2)))
    model["mean_error_px"] = float(np.mean(errors))
    model["max_error_px"] = float(np.max(errors))
    worst_local = int(np.argmax(errors))
    worst_idx, worst_item = samples[worst_local]
    model["worst_sample"] = {
        "index": int(worst_idx),
        "error_px": float(errors[worst_local]),
        "distance_cm": float(worst_item["distance_cm"]),
        "target_distance_cm": worst_item.get("target_distance_cm"),
        "target_x": worst_item.get("target_x"),
        "target_y": worst_item.get("target_y"),
    }
    return True, "fit rmse {:.2f}px n={}".format(model["rmse_px"], len(samples))


def predict_guided_point(model, rgb_x, rgb_y, distance_cm, rgb_w, rgb_h,
                         thermal_w, thermal_h):
    if model.get("coeff_x") is None or model.get("coeff_y") is None:
        return None
    features = guided_features(rgb_x, rgb_y, distance_cm, rgb_w, rgb_h)
    tx = float(np.dot(np.array(model["coeff_x"], dtype=np.float64), features))
    ty = float(np.dot(np.array(model["coeff_y"], dtype=np.float64), features))
    tx = int(max(0, min(thermal_w - 1, round(tx))))
    ty = int(max(0, min(thermal_h - 1, round(ty))))
    return tx, ty


def add_guided_sample(model, sample):
    sample["enabled"] = True
    model.setdefault("samples", []).append(sample)


def enabled_sample_count(model):
    return sum(1 for item in model.get("samples", []) if item.get("enabled", True))


def undo_last_sample(model):
    samples = model.get("samples", [])
    if not samples:
        return False
    samples.pop()
    return True


def disable_worst_sample(model):
    worst = model.get("worst_sample")
    if not worst:
        return False, "fit first"
    idx = worst.get("index")
    samples = model.get("samples", [])
    if idx is None or idx < 0 or idx >= len(samples):
        return False, "bad worst index"
    samples[idx]["enabled"] = False
    return True, "disabled sample {} err {:.2f}px".format(
        idx + 1, float(worst.get("error_px", 0.0)))


def enable_all_samples(model):
    for item in model.get("samples", []):
        item["enabled"] = True


def nearest_hotspot(thermal_pixels, around=None, radius=None):
    return find_hottest_thermal_point(thermal_pixels, around=around, radius=radius)


def get_camera():
    cap = cv2.VideoCapture(GST_PIPELINE, cv2.CAP_GSTREAMER)
    if cap.isOpened():
        return cap
    cap.release()
    fallback = cv2.VideoCapture(0)
    return fallback


def read_camera_frame(cap, attempts=15, delay_s=0.2):
    for _ in range(attempts):
        ok, frame = cap.read()
        if ok and frame is not None and getattr(frame, "size", 0):
            return frame
        time.sleep(delay_s)
    return None


def read_urm13_distance():
    bus = smbus2.SMBus(1)
    try:
        value = 0xFFFF
        for _ in range(4):
            bus.write_byte_data(URM13_ADDR, 0x0A, 0x01)
            time.sleep(0.15)
            msb = bus.read_byte_data(URM13_ADDR, 0x03)
            lsb = bus.read_byte_data(URM13_ADDR, 0x04)
            value = (msb << 8) | lsb
            if value != 0xFFFF:
                return value
        return value
    finally:
        bus.close()


def make_thermal_sensor():
    i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
    mlx = adafruit_mlx90640.MLX90640(i2c, address=MLX_ADDR)
    mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
    return mlx


def thermal_worker(state):
    frame = [0.0] * 768
    mlx = None
    while state.running:
        try:
            if mlx is None:
                mlx = make_thermal_sensor()
            mlx.getFrame(frame)
            pixels = np.array(frame, dtype=np.float32).reshape((24, 32))
            thermal = {
                "avg_c": float(np.mean(pixels)),
                "min_c": float(np.min(pixels)),
                "max_c": float(np.max(pixels)),
                "center_c": float(pixels[12, 16]),
                "pixels": pixels.copy(),
            }
            with state.lock:
                state.thermal = thermal
                state.thermal_error = None
        except Exception as exc:
            mlx = None
            with state.lock:
                state.thermal = None
                state.thermal_error = str(exc)
        time.sleep(0.5)


def distance_worker(state):
    samples = collections.deque(maxlen=URM13_MEDIAN_WINDOW)
    while state.running:
        try:
            raw_distance = read_urm13_distance()
            if URM13_MIN_RELIABLE_CM <= raw_distance <= URM13_MAX_RELIABLE_CM:
                samples.append(raw_distance)
                sorted_samples = sorted(samples)
                distance = sorted_samples[len(sorted_samples) // 2]
                status = "ok"
            elif raw_distance < URM13_MIN_RELIABLE_CM:
                distance = None
                status = "too close / unreliable"
            else:
                distance = None
                status = "out of range / unreliable"
            with state.lock:
                state.distance_cm = distance
                state.distance_status = status
                state.distance_error = None
        except Exception as exc:
            with state.lock:
                state.distance_error = str(exc)
        time.sleep(0.3)


def _bme280_read_calibration(bus):
    """Read BME280 factory calibration constants from registers."""
    tp = bus.read_i2c_block_data(BME280_ADDR, 0x88, 26)
    h1 = bus.read_byte_data(BME280_ADDR, 0xA1)
    hb = bus.read_i2c_block_data(BME280_ADDR, 0xE1, 7)
    import struct
    cal = {}
    cal["T1"] = struct.unpack_from("<H", bytes(tp), 0)[0]
    cal["T2"] = struct.unpack_from("<h", bytes(tp), 2)[0]
    cal["T3"] = struct.unpack_from("<h", bytes(tp), 4)[0]
    cal["P1"] = struct.unpack_from("<H", bytes(tp), 6)[0]
    cal["P2"] = struct.unpack_from("<h", bytes(tp), 8)[0]
    cal["P3"] = struct.unpack_from("<h", bytes(tp), 10)[0]
    cal["P4"] = struct.unpack_from("<h", bytes(tp), 12)[0]
    cal["P5"] = struct.unpack_from("<h", bytes(tp), 14)[0]
    cal["P6"] = struct.unpack_from("<h", bytes(tp), 16)[0]
    cal["P7"] = struct.unpack_from("<h", bytes(tp), 18)[0]
    cal["P8"] = struct.unpack_from("<h", bytes(tp), 20)[0]
    cal["P9"] = struct.unpack_from("<h", bytes(tp), 22)[0]
    cal["H1"] = h1
    cal["H2"] = struct.unpack_from("<h", bytes(hb), 0)[0]
    cal["H3"] = hb[2]
    cal["H4"] = (hb[3] << 4) | (hb[4] & 0x0F)
    cal["H5"] = (hb[5] << 4) | ((hb[4] >> 4) & 0x0F)
    cal["H6"] = struct.unpack_from("<b", bytes([hb[6]]))[0]
    return cal


def _bme280_compensate(adc_T, adc_P, adc_H, cal):
    """BME280 compensation formulas (Bosch datasheet section 4.2)."""
    # Temperature
    var1 = (adc_T / 16384.0 - cal["T1"] / 1024.0) * cal["T2"]
    var2 = ((adc_T / 131072.0 - cal["T1"] / 8192.0) ** 2) * cal["T3"]
    t_fine = var1 + var2
    temp_c = t_fine / 5120.0
    # Pressure
    v1 = t_fine / 2.0 - 64000.0
    v2 = v1 * v1 * cal["P6"] / 32768.0
    v2 = v2 + v1 * cal["P5"] * 2.0
    v2 = v2 / 4.0 + cal["P4"] * 65536.0
    v1 = (cal["P3"] * v1 * v1 / 524288.0 + cal["P2"] * v1) / 524288.0
    v1 = (1.0 + v1 / 32768.0) * cal["P1"]
    if v1 == 0:
        pres_hpa = 0.0
    else:
        p = 1048576.0 - adc_P
        p = ((p - v2 / 4096.0) * 6250.0) / v1
        v1 = cal["P9"] * p * p / 2147483648.0
        v2 = p * cal["P8"] / 32768.0
        p = p + (v1 + v2 + cal["P7"]) / 16.0
        pres_hpa = p / 100.0
    # Humidity
    h = t_fine - 76800.0
    if h == 0:
        hum = 0.0
    else:
        h = (adc_H - (cal["H4"] * 64.0 + cal["H5"] / 16384.0 * h)) * (
            cal["H2"] / 65536.0 * (1.0 + cal["H6"] / 67108864.0 * h * (
                1.0 + cal["H3"] / 67108864.0 * h)))
        h = h * (1.0 - cal["H1"] * h / 524288.0)
        hum = max(0.0, min(100.0, h))
    return temp_c, hum, pres_hpa


def _bme280_single_read(bus, cal):
    """Trigger a BME280 forced-mode measurement and return (temp, hum, pres)."""
    bus.write_byte_data(BME280_ADDR, 0xF2, 0x01)   # humidity oversampling x1
    bus.write_byte_data(BME280_ADDR, 0xF4, 0x25)   # temp x1, pres x1, forced
    time.sleep(0.05)
    data = bus.read_i2c_block_data(BME280_ADDR, 0xF7, 8)
    adc_P = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
    adc_T = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
    adc_H = (data[6] << 8) | data[7]
    return _bme280_compensate(adc_T, adc_P, adc_H, cal)


def ambient_worker(state):
    """Read ambient conditions from BME280 (preferred) or DHT22 (fallback).

    BME280 is an I2C sensor — reliable, fast, and provides temperature,
    humidity, and pressure.  The DHT22 is kept as a fallback only because
    it was the original sensor in the project and may still be wired up.
    """
    # --- Try BME280 first (I2C, reliable) ---
    try:
        bus = smbus2.SMBus(1)
        chip_id = bus.read_byte_data(BME280_ADDR, 0xD0)
        if chip_id != 0x60:
            raise RuntimeError("BME280 chip ID 0x{:02X} unexpected".format(chip_id))
        cal = _bme280_read_calibration(bus)
        with state.lock:
            state.ambient_error = None
            state.ambient_source = "BME280"

        while state.running:
            try:
                temp_c, hum, pres = _bme280_single_read(bus, cal)
                with state.lock:
                    state.ambient_temp_c = temp_c
                    state.ambient_humidity = hum
                    state.ambient_pressure_hpa = pres
                    state.ambient_error = None
                    state.ambient_updated_at = time.time()
            except Exception as exc:
                with state.lock:
                    state.ambient_error = str(exc)
            time.sleep(BME280_READ_INTERVAL_S)
        bus.close()
        return
    except Exception as bme_err:
        bme_msg = str(bme_err)

    # --- Fallback to DHT22 ---
    if adafruit_dht is None:
        with state.lock:
            state.ambient_error = "BME280: {} / DHT22: adafruit_dht not installed".format(bme_msg)
        return

    sensor = None
    try:
        sensor = adafruit_dht.DHT22(DHT_PIN, use_pulseio=False)
    except TypeError:
        sensor = adafruit_dht.DHT22(DHT_PIN)
    except Exception as exc:
        with state.lock:
            state.ambient_error = "BME280: {} / DHT22: {}".format(bme_msg, exc)
        return

    with state.lock:
        state.ambient_source = "DHT22"

    while state.running:
        success = False
        last_error = None
        for _ in range(3):
            try:
                temp_c = sensor.temperature
                humidity = sensor.humidity
                if temp_c is not None and humidity is not None:
                    with state.lock:
                        state.ambient_temp_c = float(temp_c)
                        state.ambient_humidity = float(humidity)
                        state.ambient_error = None
                        state.ambient_updated_at = time.time()
                    success = True
                    break
            except Exception as exc:
                last_error = str(exc)
            time.sleep(2.0)
        if not success:
            with state.lock:
                state.ambient_error = last_error or "no valid reading"
        time.sleep(5.0)


def detection_worker(state, detector):
    """Run face detection in a background thread.

    Continuously grabs the latest camera frame from state.detect_frame,
    runs the DNN/Haar detector, and posts results back to state.detections.
    This decouples detection FPS from display FPS so the camera feed stays
    smooth even though detection may only run at 3-5 FPS on CPU.
    """
    last_frame_id = -1
    det_count = 0
    det_time = time.time()
    while state.running:
        with state.lock:
            frame = state.detect_frame
            frame_id = state.detect_frame_id
        if frame is None or frame_id == last_frame_id:
            time.sleep(0.005)
            continue
        last_frame_id = frame_id
        results = detect_heads(frame, detector)
        det_count += 1
        now = time.time()
        if now - det_time >= 1.0:
            with state.lock:
                state.detect_fps = det_count / (now - det_time)
            det_count = 0
            det_time = now
        with state.lock:
            state.detections = results


def load_cascades():
    frontal = cv2.CascadeClassifier(FRONTAL_CASCADE)
    profile = cv2.CascadeClassifier(PROFILE_CASCADE)
    errors = []
    if frontal.empty():
        errors.append("frontal cascade missing: {}".format(FRONTAL_CASCADE))
    if profile.empty():
        errors.append("profile cascade missing: {}".format(PROFILE_CASCADE))
    return frontal, profile, errors


def load_detector():
    """Load DNN SSD face detector if model files exist, otherwise Haar cascades.

    The DNN SSD (ResNet-10) detector is ~20x faster than MTCNN on Nano and
    far more accurate than Haar cascades. It runs entirely inside OpenCV's
    DNN module — no TensorFlow, no extra RAM.
    """
    if os.path.isfile(_DNN_PROTO) and os.path.isfile(_DNN_MODEL):
        try:
            net = cv2.dnn.readNetFromCaffe(_DNN_PROTO, _DNN_MODEL)
            return {"backend": "dnn_ssd", "net": net}, []
        except Exception as exc:
            pass

    frontal, profile, errors = load_cascades()
    return {"backend": "haar", "frontal": frontal, "profile": profile}, errors


def detect_heads_dnn(frame_bgr, net):
    """Run OpenCV DNN SSD face detector. Fast and accurate."""
    h, w = frame_bgr.shape[:2]
    blob = cv2.dnn.blobFromImage(
        frame_bgr, 1.0, (300, 300), (104.0, 177.0, 123.0), False, False)
    net.setInput(blob)
    out = net.forward()

    detections = []
    for i in range(out.shape[2]):
        confidence = float(out[0, 0, i, 2])
        if confidence < DNN_CONFIDENCE_THRESHOLD:
            continue
        x0 = max(0, int(out[0, 0, i, 3] * w))
        y0 = max(0, int(out[0, 0, i, 4] * h))
        x1 = min(w, int(out[0, 0, i, 5] * w))
        y1 = min(h, int(out[0, 0, i, 6] * h))
        bw = x1 - x0
        bh = y1 - y0
        if bw < FACE_MIN_SIZE or bh < FACE_MIN_SIZE:
            continue
        detections.append({
            "rect": (x0, y0, bw, bh),
            "kind": "dnn_ssd",
            "confidence": confidence,
            "landmarks": {},
        })

    detections.sort(key=lambda d: d["rect"][2] * d["rect"][3], reverse=True)
    return detections


def detect_heads_haar(frame_bgr, frontal, profile):
    """Original Haar cascade detection as fallback."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    small = cv2.resize(gray, None, fx=DETECT_SCALE, fy=DETECT_SCALE)
    min_size = max(20, int(FACE_MIN_SIZE * DETECT_SCALE))

    detections = []
    for (x, y, w, h) in frontal.detectMultiScale(
        small, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
    ):
        detections.append({
            "rect": (
                int(x / DETECT_SCALE),
                int(y / DETECT_SCALE),
                int(w / DETECT_SCALE),
                int(h / DETECT_SCALE),
            ),
            "kind": "frontal",
        })

    if not detections:
        for (x, y, w, h) in profile.detectMultiScale(
            small, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
        ):
            detections.append({
                "rect": (
                    int(x / DETECT_SCALE),
                    int(y / DETECT_SCALE),
                    int(w / DETECT_SCALE),
                    int(h / DETECT_SCALE),
                ),
                "kind": "profile",
            })

    if not detections:
        flipped_small = cv2.flip(small, 1)
        for (x, y, w, h) in profile.detectMultiScale(
            flipped_small, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size)
        ):
            x = small.shape[1] - x - w
            detections.append({
                "rect": (
                    int(x / DETECT_SCALE),
                    int(y / DETECT_SCALE),
                    int(w / DETECT_SCALE),
                    int(h / DETECT_SCALE),
                ),
                "kind": "profile",
            })

    detections.sort(key=lambda item: item["rect"][2] * item["rect"][3], reverse=True)
    return detections


def detect_heads(frame_bgr, detector):
    """Unified detection: uses DNN SSD if loaded, Haar otherwise."""
    if detector["backend"] == "dnn_ssd":
        return detect_heads_dnn(frame_bgr, detector["net"])
    return detect_heads_haar(frame_bgr, detector["frontal"], detector["profile"])


def interpolate_distance_offset(alignment, distance_cm):
    samples = sorted(
        alignment.get("distance_samples", []),
        key=lambda item: item["distance_cm"])
    if not samples or distance_cm is None:
        return float(alignment["offset_x"]), float(alignment["offset_y"])

    distance_cm = float(distance_cm)
    if distance_cm <= samples[0]["distance_cm"]:
        return samples[0]["offset_x"], samples[0]["offset_y"]
    if distance_cm >= samples[-1]["distance_cm"]:
        return samples[-1]["offset_x"], samples[-1]["offset_y"]

    for left, right in zip(samples, samples[1:]):
        left_d = left["distance_cm"]
        right_d = right["distance_cm"]
        if left_d <= distance_cm <= right_d:
            span = max(1.0, right_d - left_d)
            t = (distance_cm - left_d) / span
            ox = left["offset_x"] + (right["offset_x"] - left["offset_x"]) * t
            oy = left["offset_y"] + (right["offset_y"] - left["offset_y"]) * t
            return ox, oy

    return float(alignment["offset_x"]), float(alignment["offset_y"])


def effective_alignment(alignment, distance_cm=None, nudge_x=0.0, nudge_y=0.0):
    ox, oy = interpolate_distance_offset(alignment, distance_cm)
    return {
        "scale_x": float(alignment["scale_x"]),
        "scale_y": float(alignment["scale_y"]),
        "offset_x": ox + float(nudge_x),
        "offset_y": oy + float(nudge_y),
    }


def add_distance_sample(alignment, distance_cm, effective):
    if distance_cm is None:
        return False
    distance_cm = float(distance_cm)
    sample = {
        "distance_cm": distance_cm,
        "offset_x": float(effective["offset_x"]),
        "offset_y": float(effective["offset_y"]),
    }
    samples = alignment.setdefault("distance_samples", [])
    for idx, existing in enumerate(samples):
        if abs(existing["distance_cm"] - distance_cm) <= ALIGNMENT_SAMPLE_MERGE_CM:
            samples[idx] = sample
            break
    else:
        samples.append(sample)
    samples.sort(key=lambda item: item["distance_cm"])
    return True


def map_rgb_point_to_thermal(x, y, rgb_w, rgb_h, thermal_w, thermal_h,
                             alignment=None):
    alignment = alignment or default_alignment()
    nx = float(x) / max(1.0, float(rgb_w))
    ny = float(y) / max(1.0, float(rgb_h))
    tx = nx * thermal_w * alignment["scale_x"] + alignment["offset_x"]
    ty = ny * thermal_h * alignment["scale_y"] + alignment["offset_y"]
    tx = int(max(0, min(thermal_w - 1, round(tx))))
    ty = int(max(0, min(thermal_h - 1, round(ty))))
    return tx, ty


def find_hottest_thermal_point(thermal_pixels, around=None, radius=None):
    pixels = np.array(thermal_pixels, dtype=np.float32)
    h, w = pixels.shape[:2]
    border = THERMAL_BORDER_IGNORE_PX
    finite = np.isfinite(pixels)
    if not np.any(finite):
        return None
    if not np.all(finite):
        pixels = pixels.copy()
        pixels[~finite] = float(np.min(pixels[finite]))

    # Use a small spatial average so an isolated bad/stuck pixel cannot win.
    score = cv2.blur(pixels, (3, 3))
    search_mask = np.zeros((h, w), dtype=np.uint8)

    if around is not None and radius is not None:
        cx, cy = around
        x0 = max(border, int(cx - radius))
        x1 = min(w - border, int(cx + radius + 1))
        y0 = max(border, int(cy - radius))
        y1 = min(h - border, int(cy + radius + 1))
        if x1 > x0 and y1 > y0:
            search_mask[y0:y1, x0:x1] = 1
    else:
        search_mask[border:h - border, border:w - border] = 1

    valid_values = score[search_mask.astype(bool)]
    if valid_values.size == 0:
        return None

    threshold = float(np.percentile(valid_values, THERMAL_BLOB_PERCENTILE))
    hot_mask = ((score >= threshold) & search_mask.astype(bool)).astype(np.uint8)
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(hot_mask, 8)

    best_label = None
    best_rank = None
    min_area = 1 if around is not None else THERMAL_MIN_BLOB_PIXELS
    for label in range(1, component_count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        component = labels == label
        component_scores = score[component]
        rank = (float(np.mean(component_scores)), float(np.max(component_scores)), area)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_label = label

    if best_label is not None:
        component = labels == best_label
        masked_score = np.where(component, score, -np.inf)
        y, x = np.unravel_index(int(np.argmax(masked_score)), masked_score.shape)
        return int(x), int(y)

    masked_score = np.where(search_mask.astype(bool), score, -np.inf)
    y, x = np.unravel_index(int(np.argmax(masked_score)), masked_score.shape)
    return int(x), int(y)


def rough_rgb_to_thermal_point(rgb_x, rgb_y, rgb_w, rgb_h, thermal_w, thermal_h):
    tx = int(round((float(rgb_x) / max(1.0, float(rgb_w))) * thermal_w))
    ty = int(round((float(rgb_y) / max(1.0, float(rgb_h))) * thermal_h))
    tx = max(THERMAL_BORDER_IGNORE_PX, min(thermal_w - THERMAL_BORDER_IGNORE_PX - 1, tx))
    ty = max(THERMAL_BORDER_IGNORE_PX, min(thermal_h - THERMAL_BORDER_IGNORE_PX - 1, ty))
    return tx, ty


def snap_alignment_to_thermal_point(alignment, rgb_point, thermal_point,
                                    rgb_w, rgb_h, thermal_w, thermal_h):
    mapped = map_rgb_point_to_thermal(
        rgb_point[0], rgb_point[1], rgb_w, rgb_h, thermal_w, thermal_h,
        alignment=alignment)
    alignment["offset_x"] += float(thermal_point[0] - mapped[0])
    alignment["offset_y"] += float(thermal_point[1] - mapped[1])
    return mapped


def clamp_alignment(alignment):
    alignment["scale_x"] = max(0.2, min(2.5, float(alignment["scale_x"])))
    alignment["scale_y"] = max(0.2, min(2.5, float(alignment["scale_y"])))
    alignment["offset_x"] = max(-64.0, min(64.0, float(alignment["offset_x"])))
    alignment["offset_y"] = max(-48.0, min(48.0, float(alignment["offset_y"])))
    for sample in alignment.get("distance_samples", []):
        sample["offset_x"] = max(-64.0, min(64.0, float(sample["offset_x"])))
        sample["offset_y"] = max(-48.0, min(48.0, float(sample["offset_y"])))


def get_forehead_point(landmarks, face_rect):
    """Estimate forehead centre from MTCNN landmarks.

    Clinical IR thermometers target the forehead because it has consistent
    skin exposure and minimal hair/glasses interference.  The forehead is
    roughly halfway between the top of the bounding box and the eye line.
    """
    left_eye = landmarks.get("left_eye")
    right_eye = landmarks.get("right_eye")
    if left_eye is None or right_eye is None:
        return None
    eye_y = (left_eye[1] + right_eye[1]) // 2
    eye_x = (left_eye[0] + right_eye[0]) // 2
    _, face_y, _, _ = face_rect
    forehead_y = face_y + (eye_y - face_y) // 2
    return (eye_x, forehead_y)


def extract_face_proxy(thermal_pixels, thermal_point, face_rect, rgb_shape,
                       landmarks=None, alignment=None):
    rgb_h, rgb_w = rgb_shape[:2]
    face_x, face_y, face_w, face_h = face_rect
    thermal_h, thermal_w = thermal_pixels.shape[:2]

    # If landmarks are available, target the forehead region for a more
    # clinically meaningful reading.  Otherwise fall back to face-centre.
    target_rgb = None
    if landmarks:
        target_rgb = get_forehead_point(landmarks, face_rect)

    if target_rgb is not None:
        tx, ty = map_rgb_point_to_thermal(
            target_rgb[0], target_rgb[1], rgb_w, rgb_h, thermal_w, thermal_h,
            alignment=alignment)
    else:
        tx, ty = thermal_point

    roi_w = max(2, int((float(face_w) / rgb_w) * thermal_w * 0.7))
    roi_h = max(2, int((float(face_h) / rgb_h) * thermal_h * 0.5))

    x0 = max(0, tx - roi_w // 2)
    x1 = min(thermal_w, tx + roi_w // 2 + 1)
    y0 = max(0, ty - roi_h // 2)
    y1 = min(thermal_h, ty + roi_h // 2 + 1)
    roi = thermal_pixels[y0:y1, x0:x1]
    if roi.size == 0:
        return None

    proxy_c = float(np.percentile(roi, THERMAL_PERCENTILE))
    return {
        "proxy_c": proxy_c,
        "roi_bounds": (x0, y0, x1, y1),
        "roi_min_c": float(np.min(roi)),
        "roi_max_c": float(np.max(roi)),
        "forehead_targeted": target_rgb is not None,
    }


def compute_corrected_estimate(raw_proxy_c, distance_cm, ambient_temp_c, humidity):
    if raw_proxy_c is None:
        return None

    distance_adjust_c = 0.0
    if distance_cm is not None:
        distance_adjust_c = ((distance_cm - REFERENCE_DISTANCE_CM) / 100.0) * DISTANCE_COEFF_PER_M

    ambient_adjust_c = 0.0
    if ambient_temp_c is not None:
        ambient_adjust_c = (REFERENCE_AMBIENT_C - ambient_temp_c) * AMBIENT_COEFF

    humidity_adjust_c = 0.0
    if humidity is not None:
        humidity_adjust_c = (humidity - REFERENCE_HUMIDITY) * HUMIDITY_COEFF

    corrected_proxy_c = raw_proxy_c + distance_adjust_c + ambient_adjust_c + humidity_adjust_c
    delta_c = corrected_proxy_c - NORMAL_FACE_PROXY_C

    if abs(delta_c) <= NORMAL_PROXY_TOLERANCE_C:
        status = "within expected normal proxy range"
    elif delta_c > 0:
        status = "above expected normal proxy range"
    else:
        status = "below expected normal proxy range"

    return {
        "raw_proxy_c": raw_proxy_c,
        "corrected_proxy_c": corrected_proxy_c,
        "distance_adjust_c": distance_adjust_c,
        "ambient_adjust_c": ambient_adjust_c,
        "humidity_adjust_c": humidity_adjust_c,
        "delta_c": delta_c,
        "status": status,
    }


def add_status_line(frame, text, line_no, color=(0, 255, 0)):
    y = 30 + (line_no * 28)
    cv2.putText(frame, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, (18, y), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA)


def draw_crosshair(frame, center, color=(255, 255, 255)):
    x, y = center
    cv2.line(frame, (x - 12, y), (x + 12, y), color, 1, cv2.LINE_AA)
    cv2.line(frame, (x, y - 12), (x, y + 12), color, 1, cv2.LINE_AA)


def draw_guided_target(frame, target_point, face_center=None, ok=False):
    color = (80, 255, 80) if ok else (0, 255, 255)
    x, y = target_point
    cv2.circle(frame, (x, y), TARGET_TOLERANCE_PX, color, 1, cv2.LINE_AA)
    cv2.drawMarker(frame, (x, y), color, cv2.MARKER_CROSS, 28, 2)
    if face_center is not None:
        cv2.line(frame, face_center, (x, y), color, 1, cv2.LINE_AA)


def add_thermal_inset(frame, thermal, thermal_point=None, thermal_roi=None,
                      hotspot_point=None):
    pixels = np.array(thermal["pixels"], dtype=np.float32)
    finite = np.isfinite(pixels)
    if not np.any(finite):
        return
    if not np.all(finite):
        pixels = pixels.copy()
        pixels[~finite] = float(np.min(pixels[finite]))
    norm = cv2.normalize(pixels, None, 0, 255, cv2.NORM_MINMAX)
    heat = cv2.applyColorMap(norm.astype(np.uint8), cv2.COLORMAP_INFERNO)
    if thermal_point is not None:
        cv2.circle(heat, thermal_point, 1, (255, 255, 255), -1)
    if hotspot_point is not None:
        cv2.drawMarker(
            heat, hotspot_point, (0, 255, 255), cv2.MARKER_CROSS,
            markerSize=5, thickness=1)
    if thermal_roi is not None:
        x0, y0, x1, y1 = thermal_roi
        cv2.rectangle(heat, (x0, y0), (x1 - 1, y1 - 1), (255, 255, 255), 1)
    heat = rotate_frame(heat, THERMAL_ROTATE_DEGREES)
    heat = cv2.resize(heat, THERMAL_INSET_SIZE, interpolation=cv2.INTER_NEAREST)
    x0 = 16
    y0 = frame.shape[0] - heat.shape[0] - THERMAL_INSET_MARGIN
    frame[y0:y0 + heat.shape[0], x0:x0 + heat.shape[1]] = heat
    cv2.rectangle(frame, (x0, y0), (x0 + heat.shape[1], y0 + heat.shape[0]), (255, 255, 255), 2)
    cv2.putText(frame, "MLX90640", (x0 + 10, y0 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)


def _sidebar_text(sidebar, row, text, color=(200, 200, 200)):
    """Draw a single line of text on the sidebar panel."""
    y = SIDEBAR_MARGIN_Y + row * SIDEBAR_LINE_HEIGHT
    cv2.putText(sidebar, text, (SIDEBAR_MARGIN_X, y),
                SIDEBAR_FONT, SIDEBAR_FONT_SCALE, color, 1, cv2.LINE_AA)


def _sidebar_heading(sidebar, row, text):
    """Draw a section heading on the sidebar (brighter, slightly larger)."""
    y = SIDEBAR_MARGIN_Y + row * SIDEBAR_LINE_HEIGHT
    cv2.putText(sidebar, text, (SIDEBAR_MARGIN_X, y),
                SIDEBAR_FONT, 0.52, (255, 255, 255), 1, cv2.LINE_AA)


def draw_sidebar(sidebar, info):
    """Render all sensor and status information onto the sidebar panel.

    `info` is a dict with all the current state values needed for display.
    """
    row = 0

    # --- Tracking section ---
    _sidebar_heading(sidebar, row, "TRACKING")
    row += 1
    mode = "Alignment Test" if info.get("servo_disabled") else (
        "Auto-Track" if info.get("auto_track") else "Manual")
    _sidebar_text(sidebar, row, "Mode: {}".format(mode))
    row += 1
    if info.get("servo_disabled"):
        _sidebar_text(sidebar, row, "Servos: disabled", (0, 255, 255))
        row += 1
    else:
        _sidebar_text(sidebar, row, "Horizontal: {:.1f} deg".format(info.get("tilt_angle", 0)))
        row += 1
        _sidebar_text(sidebar, row, "Vertical: {:.1f} deg".format(info.get("pan_angle", 0)))
        row += 1
    _sidebar_text(sidebar, row, "Detector: {}".format(info.get("detector_backend", "?")))
    row += 1
    if info.get("calibration_mode"):
        _sidebar_text(sidebar, row, "Alignment: CALIBRATING", (0, 255, 255))
    else:
        _sidebar_text(sidebar, row, "Alignment: active")
    row += 1

    # --- Detection section ---
    row += 1
    _sidebar_heading(sidebar, row, "DETECTION")
    row += 1
    det_count = info.get("det_count", 0)
    _sidebar_text(sidebar, row, "Faces: {}".format(det_count),
                  (80, 255, 80) if det_count > 0 else (0, 165, 255))
    row += 1

    correction = info.get("correction")
    if correction is not None:
        _sidebar_text(sidebar, row, "Raw proxy: {:.2f} C".format(correction["raw_proxy_c"]))
        row += 1
        _sidebar_text(sidebar, row, "Corrected: {:.2f} C".format(correction["corrected_proxy_c"]))
        row += 1
        delta = correction["delta_c"]
        _sidebar_text(sidebar, row, "vs Normal: {:+.2f} C".format(delta))
        row += 1
        status = correction["status"]
        if "within" in status:
            a_color = (80, 255, 80)
        elif "above" in status:
            a_color = (0, 165, 255)
        else:
            a_color = (255, 200, 100)
        _sidebar_text(sidebar, row, "{}".format(status), a_color)
        row += 1
    elif det_count > 0:
        _sidebar_text(sidebar, row, "Thermal mapping unavailable", (0, 165, 255))
        row += 1
    else:
        _sidebar_text(sidebar, row, "No target detected", (0, 165, 255))
        row += 1

    # --- Sensors section ---
    row += 1
    _sidebar_heading(sidebar, row, "SENSORS")
    row += 1

    # Distance
    distance_cm = info.get("distance_cm")
    distance_error = info.get("distance_error")
    distance_status = info.get("distance_status")
    if distance_cm is not None and distance_status == "ok":
        _sidebar_text(sidebar, row, "Distance: {} cm".format(distance_cm))
    elif distance_error:
        _sidebar_text(sidebar, row, "Distance: {}".format(distance_error), (0, 165, 255))
    else:
        _sidebar_text(sidebar, row, "Distance: {}".format(distance_status or "waiting"), (0, 165, 255))
    row += 1

    # Ambient
    ambient_temp = info.get("ambient_temp_c")
    ambient_hum = info.get("ambient_humidity")
    ambient_pres = info.get("ambient_pressure")
    ambient_source = info.get("ambient_source")
    ambient_error = info.get("ambient_error")
    ambient_updated = info.get("ambient_updated_at")
    if ambient_temp is not None and ambient_hum is not None:
        age_s = int(time.time() - ambient_updated) if ambient_updated else 0
        _sidebar_text(sidebar, row, "Temp: {:.1f} C  Hum: {:.1f}%".format(ambient_temp, ambient_hum))
        row += 1
        pres_str = ""
        if ambient_pres is not None:
            pres_str = "Pres: {:.0f} hPa  ".format(ambient_pres)
        src_str = "[{}]".format(ambient_source) if ambient_source else ""
        _sidebar_text(sidebar, row, "{}{} ({}s ago)".format(pres_str, src_str, age_s))
    else:
        _sidebar_text(sidebar, row, "Ambient: {}".format(ambient_error or "waiting"), (0, 165, 255))
    row += 1

    # Thermal
    thermal = info.get("thermal")
    thermal_error = info.get("thermal_error")
    if thermal is not None:
        _sidebar_text(sidebar, row, "Thermal avg: {:.1f} C".format(thermal["avg_c"]))
        row += 1
        _sidebar_text(sidebar, row, "Thermal min: {:.1f}  max: {:.1f} C".format(
            thermal["min_c"], thermal["max_c"]))
    elif thermal_error:
        _sidebar_text(sidebar, row, "Thermal: {}".format(thermal_error), (0, 165, 255))
    else:
        _sidebar_text(sidebar, row, "Thermal: waiting", (0, 165, 255))
    row += 1

    # --- Footer ---
    row += 1
    det_errors = info.get("detector_errors")
    if det_errors:
        _sidebar_text(sidebar, row, "Det error: {}".format(det_errors), (0, 0, 255))
        row += 1

    # FPS
    row += 1
    _sidebar_heading(sidebar, row, "PERFORMANCE")
    row += 1
    _sidebar_text(sidebar, row, "Display: {:.1f} FPS".format(info.get("display_fps", 0)), (0, 255, 255))
    row += 1
    _sidebar_text(sidebar, row, "Detect:  {:.1f} FPS".format(info.get("detect_fps", 0)), (0, 255, 255))
    row += 1

    # Guided calibration
    row += 1
    guided = info.get("guided_model")
    if guided is not None:
        _sidebar_heading(sidebar, row, "GUIDED CAL")
        row += 1
        step = info.get("guided_step")
        step_idx = info.get("guided_step_idx", 0)
        step_total = info.get("guided_step_total", 0)
        if step:
            _sidebar_text(sidebar, row, "Step: {}/{}".format(step_idx + 1, step_total))
            row += 1
            _sidebar_text(sidebar, row, "Aim: {:.0f}cm  x{:.2f} y{:.2f}".format(
                step["distance_cm"], step["target_x"], step["target_y"]))
            row += 1
        _sidebar_text(sidebar, row, "Samples: {}/{}".format(
            enabled_sample_count(guided), len(guided.get("samples", []))))
        row += 1
        err = info.get("guided_live_error")
        if err is not None:
            _sidebar_text(sidebar, row, "Live err: {:.2f}px".format(err))
            row += 1
        if guided.get("rmse_px") is not None:
            _sidebar_text(sidebar, row, "Fit rmse: {:.2f}px".format(guided["rmse_px"]))
            row += 1
        worst = guided.get("worst_sample")
        if worst:
            _sidebar_text(sidebar, row, "Worst: #{} {:.1f}px".format(
                int(worst["index"]) + 1, worst["error_px"]))
            row += 1
        target_ok = info.get("target_ok")
        distance_ok = info.get("distance_ok")
        _sidebar_text(sidebar, row, "Target: {}  Dist: {}".format(
            "ok" if target_ok else "move", "ok" if distance_ok else "move"))
        row += 1
        msg = info.get("alignment_message")
        if msg:
            _sidebar_text(sidebar, row, msg[:34], (0, 255, 255))
            row += 1

    # Keys help
    row += 1
    if info.get("calibration_mode"):
        _sidebar_text(sidebar, row, "a:capture  u:undo  f:fit", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "w:disable worst  v:enable all", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "s:save  n/b:step  r:reset", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "q:quit  servos disabled", (140, 140, 140))
    else:
        _sidebar_text(sidebar, row, "c:calibrate  q:quit", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "servos disabled", (140, 140, 140))


def choose_tracking_step(error_px, deadband_px):
    """Proportional tracking step with deadband and gain limiting.

    Instead of snapping to integer steps based on multiples of deadband,
    this uses a proportional gain so the correction is smaller when the
    error is small — preventing overshoot oscillation when detection runs
    in a background thread and results are slightly stale.
    """
    if abs(error_px) <= deadband_px:
        return 0.0
    # Proportional correction: scales with error size.
    # Small error near deadband = gentle nudge, large error at edge = aggressive chase.
    excess = abs(error_px) - deadband_px
    step = excess * TRACK_GAIN / max(1.0, float(deadband_px))
    step = min(step, float(TRACK_MAX_STEP))
    step = max(0.5, step)
    return step if error_px > 0 else -step


def run_once():
    detector, detector_errors = load_detector()
    guided_model, guided_error = load_guided_model()

    result = {
        "detector": detector["backend"],
        "detector_errors": detector_errors or "ok",
        "camera": False,
        "distance_cm": None,
        "thermal": None,
        "ambient": None,
        "guided_samples": len(guided_model.get("samples", [])),
        "guided_model_status": guided_error or "loaded",
    }

    cap = get_camera()
    if cap.isOpened():
        frame = read_camera_frame(cap)
        result["camera"] = frame is not None
        if frame is not None and not detector_errors:
            detections = detect_heads(transform_main_camera_frame(frame), detector)
            result["detections"] = len(detections)
        cap.release()

    try:
        result["distance_cm"] = read_urm13_distance()
    except Exception as exc:
        result["distance_error"] = str(exc)

    try:
        mlx = make_thermal_sensor()
        frame = [0.0] * 768
        mlx.getFrame(frame)
        pixels = np.array(frame, dtype=np.float32).reshape((24, 32))
        result["thermal"] = {
            "avg_c": float(np.mean(pixels)),
            "center_c": float(pixels[12, 16]),
            "min_c": float(np.min(pixels)),
            "max_c": float(np.max(pixels)),
        }
    except Exception as exc:
        result["thermal_error"] = str(exc)

    # BME280 ambient test (preferred), DHT22 fallback
    try:
        amb_bus = smbus2.SMBus(1)
        cid = amb_bus.read_byte_data(BME280_ADDR, 0xD0)
        if cid == 0x60:
            cal = _bme280_read_calibration(amb_bus)
            t, h, p = _bme280_single_read(amb_bus, cal)
            result["ambient"] = {"temp_c": t, "humidity": h, "pressure_hpa": p, "source": "BME280"}
        else:
            result["ambient_error"] = "BME280 chip ID 0x{:02X} unexpected".format(cid)
        amb_bus.close()
    except Exception as exc:
        result["ambient_error"] = "BME280: {}".format(exc)
        # DHT22 fallback
        if adafruit_dht is not None:
            try:
                try:
                    dht = adafruit_dht.DHT22(DHT_PIN, use_pulseio=False)
                except TypeError:
                    dht = adafruit_dht.DHT22(DHT_PIN)
                temp_c = dht.temperature
                humidity = dht.humidity
                result["ambient"] = {"temp_c": temp_c, "humidity": humidity, "source": "DHT22"}
                result.pop("ambient_error", None)
                dht.exit()
            except Exception as dht_exc:
                result["ambient_error"] += " / DHT22: {}".format(dht_exc)

    result["servo"] = "disabled in alignment calibrator"

    for key in sorted(result.keys()):
        print("{}: {}".format(key, result[key]))


def run_ui():
    detector, detector_errors = load_detector()
    guided_model, guided_error = load_guided_model()
    guided_steps = make_guided_steps()
    guided_step_idx = min(
        len(guided_model.get("samples", [])), max(0, len(guided_steps) - 1))
    alignment_message = guided_error or "guided model loaded"

    state = SharedState()
    thermal_thread = threading.Thread(target=thermal_worker, args=(state,), daemon=True)
    distance_thread = threading.Thread(target=distance_worker, args=(state,), daemon=True)
    ambient_thread = threading.Thread(target=ambient_worker, args=(state,), daemon=True)
    detect_thread = threading.Thread(
        target=detection_worker, args=(state, detector), daemon=True)
    thermal_thread.start()
    distance_thread.start()
    ambient_thread.start()
    if not detector_errors:
        detect_thread.start()

    cap = get_camera()
    if not cap.isOpened():
        state.running = False
        raise RuntimeError("Could not open camera")

    pan_angle = 0.0
    tilt_angle = 0.0
    auto_track = False
    calibration_mode = True
    live_hotspot = None
    predicted_thermal_point = None
    guided_live_error = None

    # FPS tracking
    fps_time = time.time()
    fps_count = 0
    fps_display = 0.0

    try:
        while True:
            loop_start = time.time()
            frame = read_camera_frame(cap, attempts=3, delay_s=0.05)
            if frame is None:
                cap.release()
                time.sleep(0.3)
                cap = get_camera()
                if not cap.isOpened():
                    raise RuntimeError("Could not open camera")
                frame = read_camera_frame(cap, attempts=15, delay_s=0.15)
                if frame is None:
                    raise RuntimeError("Could not read camera frame")

            frame = transform_main_camera_frame(frame)
            rgb_h, rgb_w = frame.shape[:2]
            center = (rgb_w // 2, rgb_h // 2)
            draw_crosshair(frame, center)

            # Post frame to detection thread and grab latest results.
            with state.lock:
                state.detect_frame = frame.copy()
                state.detect_frame_id += 1
                detections = list(state.detections)
                detect_fps = state.detect_fps
                distance_cm = state.distance_cm
                distance_status = state.distance_status
                distance_error = state.distance_error
                thermal = state.thermal.copy() if state.thermal else None
                thermal_error = state.thermal_error
                ambient_temp_c = state.ambient_temp_c
                ambient_humidity = state.ambient_humidity
                ambient_pressure = state.ambient_pressure_hpa
                ambient_source = state.ambient_source
                ambient_error = state.ambient_error
                ambient_updated_at = state.ambient_updated_at
            best_detection = detections[0] if detections else None
            correction = None
            thermal_point = None
            thermal_roi = None
            live_hotspot = None
            predicted_thermal_point = None
            guided_live_error = None
            guided_step = guided_steps[guided_step_idx] if guided_steps else None
            target_point = None
            target_ok = False
            distance_ok = False
            if guided_step is not None:
                target_point = (
                    int(round(guided_step["target_x"] * rgb_w)),
                    int(round(guided_step["target_y"] * rgb_h)),
                )
                if distance_cm is not None:
                    distance_ok = abs(distance_cm - guided_step["distance_cm"]) <= DISTANCE_TOLERANCE_CM

            if best_detection is not None:
                x, y, w, h = best_detection["rect"]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (80, 255, 80), 2)
                label = best_detection["kind"]
                conf = best_detection.get("confidence")
                if conf is not None:
                    label = "{} {:.0f}%".format(label, conf * 100)
                cv2.putText(
                    frame, label,
                    (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 255, 80), 2, cv2.LINE_AA,
                )

                # Draw MTCNN landmarks when available.
                landmarks = best_detection.get("landmarks", {})
                for lm_name, lm_pt in landmarks.items():
                    color = (0, 255, 255) if "eye" in lm_name else (255, 200, 100)
                    cv2.circle(frame, lm_pt, 3, color, -1)

                face_center = (x + w // 2, y + h // 2)
                cv2.circle(frame, face_center, 4, (80, 255, 80), -1)
                if target_point is not None:
                    target_error = np.hypot(
                        face_center[0] - target_point[0],
                        face_center[1] - target_point[1])
                    target_ok = target_error <= TARGET_TOLERANCE_PX

                if thermal is not None:
                    predicted_thermal_point = predict_guided_point(
                        guided_model,
                        face_center[0], face_center[1], distance_cm,
                        rgb_w, rgb_h,
                        thermal["pixels"].shape[1], thermal["pixels"].shape[0],
                    )
                    # Calibration capture must not depend on the current model:
                    # if an old/bad model is loaded, local search around its
                    # prediction would stop us collecting the samples needed to
                    # repair it. Always show/capture the robust full-frame warm
                    # blob; use the fitted prediction only as a comparison point.
                    live_hotspot = nearest_hotspot(thermal["pixels"])
                    if predicted_thermal_point is not None:
                        thermal_point = predicted_thermal_point
                        if live_hotspot is not None:
                            guided_live_error = float(np.hypot(
                                predicted_thermal_point[0] - live_hotspot[0],
                                predicted_thermal_point[1] - live_hotspot[1]))
                        proxy = extract_face_proxy(
                            thermal["pixels"], thermal_point,
                            best_detection["rect"], frame.shape,
                            landmarks=landmarks,
                        )
                        if proxy is not None:
                            thermal_roi = proxy["roi_bounds"]
                            correction = compute_corrected_estimate(
                                proxy["proxy_c"], distance_cm, ambient_temp_c, ambient_humidity
                            )

            if target_point is not None:
                draw_guided_target(
                    frame, target_point,
                    face_center if best_detection is not None else None,
                    ok=target_ok and distance_ok)

            # Thermal inset stays on the camera frame.
            if thermal is not None:
                add_thermal_inset(
                    frame, thermal,
                    thermal_point=predicted_thermal_point,
                    thermal_roi=thermal_roi,
                    hotspot_point=live_hotspot)

            # FPS counter
            fps_count += 1
            now = time.time()
            if now - fps_time >= 1.0:
                fps_display = fps_count / (now - fps_time)
                fps_count = 0
                fps_time = now

            # Build sidebar with all sensor/status info.
            sidebar = np.full((rgb_h, SIDEBAR_WIDTH, 3), SIDEBAR_BG, dtype=np.uint8)
            draw_sidebar(sidebar, {
                "auto_track": auto_track,
                "tilt_angle": tilt_angle,
                "pan_angle": pan_angle,
                "detector_backend": detector["backend"],
                "det_count": len(detections),
                "correction": correction,
                "distance_cm": distance_cm,
                "distance_status": distance_status,
                "distance_error": distance_error,
                "ambient_temp_c": ambient_temp_c,
                "ambient_humidity": ambient_humidity,
                "ambient_pressure": ambient_pressure,
                "ambient_source": ambient_source,
                "ambient_error": ambient_error,
                "ambient_updated_at": ambient_updated_at,
                "thermal": thermal,
                "thermal_error": thermal_error,
                "detector_errors": detector_errors,
                "display_fps": fps_display,
                "detect_fps": detect_fps,
                "guided_model": guided_model,
                "guided_step": guided_step,
                "guided_step_idx": guided_step_idx,
                "guided_step_total": len(guided_steps),
                "guided_live_error": guided_live_error,
                "target_ok": target_ok,
                "distance_ok": distance_ok,
                "alignment_message": alignment_message,
                "calibration_mode": calibration_mode,
                "servo_disabled": True,
            })

            # Composite: camera frame + sidebar.
            display = np.hstack([frame, sidebar])
            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKeyEx(1)
            if key in (27, ord("q")):
                break
            if key == ord("a"):
                if best_detection is None or thermal is None or live_hotspot is None:
                    alignment_message = "need face + thermal"
                elif distance_cm is None:
                    alignment_message = "need distance"
                else:
                    x, y, w, h = best_detection["rect"]
                    rgb_point = (x + w // 2, y + h // 2)
                    sample = {
                        "rgb_x": float(rgb_point[0]),
                        "rgb_y": float(rgb_point[1]),
                        "rgb_w": float(rgb_w),
                        "rgb_h": float(rgb_h),
                        "thermal_x": float(live_hotspot[0]),
                        "thermal_y": float(live_hotspot[1]),
                        "thermal_w": float(thermal["pixels"].shape[1]),
                        "thermal_h": float(thermal["pixels"].shape[0]),
                        "distance_cm": float(distance_cm),
                        "target_x": float(guided_step["target_x"]) if guided_step else None,
                        "target_y": float(guided_step["target_y"]) if guided_step else None,
                        "target_distance_cm": float(guided_step["distance_cm"]) if guided_step else None,
                        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    add_guided_sample(guided_model, sample)
                    ok, msg = fit_guided_model(guided_model, rgb_w, rgb_h)
                    guided_step_idx = min(guided_step_idx + 1, max(0, len(guided_steps) - 1))
                    alignment_message = "captured; " + msg
            elif key == ord("f"):
                ok, alignment_message = fit_guided_model(guided_model, rgb_w, rgb_h)
            elif key == ord("u"):
                if undo_last_sample(guided_model):
                    ok, msg = fit_guided_model(guided_model, rgb_w, rgb_h)
                    alignment_message = "undid last; " + msg
                    guided_step_idx = max(0, min(
                        len(guided_model.get("samples", [])),
                        max(0, len(guided_steps) - 1)))
                else:
                    alignment_message = "no sample to undo"
            elif key == ord("w"):
                ok, msg = disable_worst_sample(guided_model)
                if ok:
                    fit_ok, fit_msg = fit_guided_model(guided_model, rgb_w, rgb_h)
                    alignment_message = msg + "; " + fit_msg
                else:
                    alignment_message = msg
            elif key == ord("v"):
                enable_all_samples(guided_model)
                ok, alignment_message = fit_guided_model(guided_model, rgb_w, rgb_h)
            elif key == ord("s"):
                try:
                    save_guided_model(guided_model)
                    alignment_message = "saved guided model"
                except Exception as exc:
                    alignment_message = "save failed: {}".format(exc)
            elif key == ord("r"):
                guided_model = default_guided_model()
                guided_step_idx = 0
                alignment_message = "reset guided samples"
            elif key == ord("n"):
                guided_step_idx = min(guided_step_idx + 1, max(0, len(guided_steps) - 1))
                alignment_message = "skipped step"
            elif key == ord("b"):
                guided_step_idx = max(0, guided_step_idx - 1)
                alignment_message = "previous step"
    finally:
        state.running = False
        cap.release()
        cv2.destroyAllWindows()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-once", action="store_true", help="Run a one-shot hardware sanity check")
    args = parser.parse_args()

    if args.test_once:
        run_once()
        return

    run_ui()


if __name__ == "__main__":
    main()
