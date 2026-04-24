#!/usr/bin/env python3
"""Integrated RGB/thermal/depth pan-tilt prototype for the Jetson Nano.

This is a fresh standalone app for the project brief:
- RGB camera head/face detection
- pan/tilt centering
- thermal region extraction from the aligned face position
- URM13 distance integration
- BME280 ambient temperature/humidity/pressure integration (replaces DHT22)
- simple correction model against a configurable "normal" face heat proxy

The correction model is intentionally heuristic for now. It is structured so
the coefficients can later be replaced with an empirically fitted model once
the hardware and dataset are stable.
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
WINDOW_NAME = "Thermal Depth Alignment"
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
THERMAL_BORDER_IGNORE_PX = 2
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
    }


def load_alignment(path=ALIGNMENT_FILE):
    alignment = default_alignment()
    try:
        with open(path, "r") as fh:
            saved = json.load(fh)
        for key in alignment:
            if key in saved:
                alignment[key] = float(saved[key])
        return alignment, None
    except IOError:
        return alignment, "no saved alignment"
    except Exception as exc:
        return alignment, "alignment load failed: {}".format(exc)


def save_alignment(alignment, path=ALIGNMENT_FILE):
    payload = dict(alignment)
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


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
        return model, "no guided model"
    except Exception as exc:
        return model, "guided model load failed: {}".format(exc)


def guided_model_ready(model):
    return model.get("coeff_x") is not None and model.get("coeff_y") is not None


def guided_features(rgb_x, rgb_y, distance_cm, rgb_w, rgb_h):
    x = float(rgb_x) / max(1.0, float(rgb_w))
    y = float(rgb_y) / max(1.0, float(rgb_h))
    z = 1.0 / max(1.0, float(distance_cm or REFERENCE_DISTANCE_CM))
    return np.array([1.0, x, y, z, x * z, y * z, x * y, x * x, y * y], dtype=np.float64)


def predict_guided_point(model, rgb_x, rgb_y, distance_cm, rgb_w, rgb_h,
                         thermal_w, thermal_h):
    if not guided_model_ready(model):
        return None
    features = guided_features(rgb_x, rgb_y, distance_cm, rgb_w, rgb_h)
    tx = float(np.dot(np.array(model["coeff_x"], dtype=np.float64), features))
    ty = float(np.dot(np.array(model["coeff_y"], dtype=np.float64), features))
    tx = int(max(0, min(thermal_w - 1, round(tx))))
    ty = int(max(0, min(thermal_h - 1, round(ty))))
    return tx, ty


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
    mode = "Auto-Track" if info.get("auto_track") else "Manual"
    _sidebar_text(sidebar, row, "Mode: {}".format(mode))
    row += 1
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

    # Alignment calibration
    row += 1
    alignment = info.get("alignment")
    if alignment is not None:
        _sidebar_heading(sidebar, row, "ALIGNMENT")
        row += 1
        guided = info.get("guided_model")
        if guided_model_ready(guided or {}):
            _sidebar_text(sidebar, row, "Mode: guided RGB+distance")
            row += 1
            _sidebar_text(sidebar, row, "RMSE: {:.2f}px  n:{}".format(
                guided.get("rmse_px", 0.0), len(guided.get("samples", []))))
        else:
            _sidebar_text(sidebar, row, "Mode: guided model missing", (0, 0, 255))
        row += 1
        msg = info.get("alignment_message")
        if msg:
            _sidebar_text(sidebar, row, msg[:34], (0, 255, 255))
            row += 1

    # Keys help
    row += 1
    if info.get("calibration_mode"):
        _sidebar_text(sidebar, row, "i/k/j/l:nudge  g:hotspot", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "x/X,y/Y:scale  s:save", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "r:reset  c:done  q:quit", (140, 140, 140))
    else:
        _sidebar_text(sidebar, row, "t:track", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "space:center  arrows:manual", (140, 140, 140))
        row += 1
        _sidebar_text(sidebar, row, "q:quit", (140, 140, 140))


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
    alignment, alignment_error = load_alignment()
    guided_model, guided_error = load_guided_model()

    result = {
        "detector": detector["backend"],
        "detector_errors": detector_errors or "ok",
        "camera": False,
        "distance_cm": None,
        "thermal": None,
        "ambient": None,
        "alignment": alignment,
        "alignment_status": alignment_error or "loaded",
        "guided_alignment_status": guided_error or "loaded",
        "guided_alignment_ready": guided_model_ready(guided_model),
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

    pan_servo = HardwarePWMServo(PAN_PWM, SERVO_CHIP, 2)
    tilt_servo = HardwarePWMServo(TILT_PWM, SERVO_CHIP, 0)
    if pan_servo.active:
        pan_servo.set_angle(DEFAULT_PAN_ANGLE)
    if tilt_servo.active:
        tilt_servo.set_angle(DEFAULT_TILT_ANGLE)
    result["servo"] = {
        "pan_ready": pan_servo.active,
        "tilt_ready": tilt_servo.active,
        "pan_error": pan_servo.error,
        "tilt_error": tilt_servo.error,
    }
    pan_servo.stop()
    tilt_servo.stop()

    for key in sorted(result.keys()):
        print("{}: {}".format(key, result[key]))


def run_ui():
    detector, detector_errors = load_detector()
    alignment, alignment_error = load_alignment()
    guided_model, guided_error = load_guided_model()
    if not guided_model_ready(guided_model):
        raise RuntimeError(
            "guided thermal/RGB alignment model is required; expected {}".format(
                GUIDED_ALIGNMENT_FILE))
    alignment_message = "guided model loaded"

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

    pan_servo = HardwarePWMServo(PAN_PWM, SERVO_CHIP, 2)
    tilt_servo = HardwarePWMServo(TILT_PWM, SERVO_CHIP, 0)
    pan_angle = DEFAULT_PAN_ANGLE
    tilt_angle = DEFAULT_TILT_ANGLE
    if pan_servo.active:
        pan_servo.set_angle(pan_angle)
    if tilt_servo.active:
        tilt_servo.set_angle(tilt_angle)
    last_track_time = 0.0
    tilt_last_move_time = time.time()
    pan_last_move_time = time.time()
    prev_face_center = None  # for detection jump filtering
    auto_track = True
    calibration_mode = False
    calibration_rgb_point = None
    calibration_hotspot = None

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
            calibration_rgb_point = None
            calibration_hotspot = None

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
                calibration_rgb_point = face_center

                if thermal is not None:
                    thermal_point = predict_guided_point(
                        guided_model,
                        face_center[0], face_center[1],
                        distance_cm,
                        rgb_w, rgb_h,
                        thermal["pixels"].shape[1], thermal["pixels"].shape[0],
                    )
                    if thermal_point is None:
                        raise RuntimeError("guided thermal/RGB prediction failed")
                    if calibration_mode:
                        calibration_hotspot = find_hottest_thermal_point(
                            thermal["pixels"], around=thermal_point,
                            radius=ALIGNMENT_HOTSPOT_RADIUS)
                    proxy = extract_face_proxy(
                        thermal["pixels"], thermal_point,
                        best_detection["rect"], frame.shape,
                        landmarks=None,
                        alignment=None,
                    )
                    if proxy is not None:
                        thermal_roi = proxy["roi_bounds"]
                        correction = compute_corrected_estimate(
                            proxy["proxy_c"], distance_cm, ambient_temp_c, ambient_humidity
                        )

                if auto_track and time.time() - last_track_time >= TRACK_UPDATE_S:
                    # Filter detection outliers: if the face centre jumped
                    # too far from where it was last frame, the detection is
                    # probably a false positive or a glitch — skip it so the
                    # servo doesn't lurch to a random position.
                    jump_ok = True
                    if prev_face_center is not None:
                        dx = abs(face_center[0] - prev_face_center[0])
                        dy = abs(face_center[1] - prev_face_center[1])
                        if dx > DETECTION_JUMP_THRESHOLD or dy > DETECTION_JUMP_THRESHOLD:
                            jump_ok = False
                    prev_face_center = face_center

                    if jump_ok:
                        error_x = face_center[0] - center[0]
                        error_y = face_center[1] - center[1]
                        # Scale deadband with face size: when close (big face),
                        # the servos' coarse steps would constantly hunt around
                        # centre.  A bigger deadband lets it settle.
                        deadband_x = max(TRACK_DEADBAND_MIN_X,
                                         int(w * TRACK_DEADBAND_FACE_RATIO))
                        deadband_y = max(TRACK_DEADBAND_MIN_Y,
                                         int(h * TRACK_DEADBAND_FACE_RATIO))
                        horizontal_step = choose_tracking_step(error_x, deadband_x)
                        vertical_step = choose_tracking_step(error_y, deadband_y)
                        if horizontal_step:
                            tilt_angle = clamp_angle(tilt_angle + (horizontal_step * HORIZONTAL_TRACK_SIGN))
                            tilt_servo.set_angle(tilt_angle)
                            tilt_last_move_time = time.time()
                        elif time.time() - tilt_last_move_time >= SERVO_RELAX_DELAY:
                            tilt_servo.relax()
                        if vertical_step:
                            pan_angle = clamp_angle(pan_angle + (vertical_step * VERTICAL_TRACK_SIGN))
                            pan_servo.set_angle(pan_angle)
                            pan_last_move_time = time.time()
                        elif time.time() - pan_last_move_time >= SERVO_RELAX_DELAY:
                            pan_servo.relax()
                    last_track_time = time.time()

            # Thermal inset stays on the camera frame.
            if thermal is not None:
                add_thermal_inset(
                    frame, thermal,
                    thermal_point=thermal_point,
                    thermal_roi=thermal_roi,
                    hotspot_point=calibration_hotspot if calibration_mode else None)

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
                "alignment": alignment,
                "guided_model": guided_model,
                "alignment_message": alignment_message,
                "calibration_mode": calibration_mode,
            })

            # Composite: camera frame + sidebar.
            display = np.hstack([frame, sidebar])
            cv2.imshow(WINDOW_NAME, display)
            key = cv2.waitKeyEx(1)
            if key in (27, ord("q")):
                break
            if key == ord("c"):
                calibration_mode = False
                alignment_message = "guided model required; old calibration disabled"
            elif key == ord("t"):
                auto_track = not auto_track
            elif calibration_mode and key == ord("g"):
                if thermal is not None and calibration_rgb_point is not None:
                    hot = find_hottest_thermal_point(thermal["pixels"])
                    before = snap_alignment_to_thermal_point(
                        alignment, calibration_rgb_point, hot,
                        rgb_w, rgb_h,
                        thermal["pixels"].shape[1], thermal["pixels"].shape[0])
                    clamp_alignment(alignment)
                    alignment_message = "snapped {} -> {}".format(before, hot)
                else:
                    alignment_message = "need face + thermal"
            elif calibration_mode and key == ord("s"):
                try:
                    save_alignment(alignment)
                    alignment_message = "saved alignment"
                except Exception as exc:
                    alignment_message = "save failed: {}".format(exc)
            elif calibration_mode and key == ord("r"):
                alignment = default_alignment()
                alignment_message = "reset alignment"
            elif calibration_mode and key == ord("j"):
                alignment["offset_x"] -= ALIGNMENT_NUDGE_PX
                clamp_alignment(alignment)
                alignment_message = "offset x -"
            elif calibration_mode and key == ord("l"):
                alignment["offset_x"] += ALIGNMENT_NUDGE_PX
                clamp_alignment(alignment)
                alignment_message = "offset x +"
            elif calibration_mode and key == ord("i"):
                alignment["offset_y"] -= ALIGNMENT_NUDGE_PX
                clamp_alignment(alignment)
                alignment_message = "offset y -"
            elif calibration_mode and key == ord("k"):
                alignment["offset_y"] += ALIGNMENT_NUDGE_PX
                clamp_alignment(alignment)
                alignment_message = "offset y +"
            elif calibration_mode and key == ord("x"):
                alignment["scale_x"] -= ALIGNMENT_SCALE_STEP
                clamp_alignment(alignment)
                alignment_message = "scale x -"
            elif calibration_mode and key == ord("X"):
                alignment["scale_x"] += ALIGNMENT_SCALE_STEP
                clamp_alignment(alignment)
                alignment_message = "scale x +"
            elif calibration_mode and key == ord("y"):
                alignment["scale_y"] -= ALIGNMENT_SCALE_STEP
                clamp_alignment(alignment)
                alignment_message = "scale y -"
            elif calibration_mode and key == ord("Y"):
                alignment["scale_y"] += ALIGNMENT_SCALE_STEP
                clamp_alignment(alignment)
                alignment_message = "scale y +"
            elif key == 32:
                pan_angle = DEFAULT_PAN_ANGLE
                tilt_angle = DEFAULT_TILT_ANGLE
                pan_servo.set_angle(pan_angle)
                tilt_servo.set_angle(tilt_angle)
            elif key in (81, 2424832):
                auto_track = False
                tilt_angle = clamp_angle(tilt_angle - MANUAL_STEP_DEG)
                tilt_servo.set_angle(tilt_angle)
            elif key in (83, 2555904):
                auto_track = False
                tilt_angle = clamp_angle(tilt_angle + MANUAL_STEP_DEG)
                tilt_servo.set_angle(tilt_angle)
            elif key in (82, 2490368):
                auto_track = False
                pan_angle = clamp_angle(pan_angle - MANUAL_STEP_DEG)
                pan_servo.set_angle(pan_angle)
            elif key in (84, 2621440):
                auto_track = False
                pan_angle = clamp_angle(pan_angle + MANUAL_STEP_DEG)
                pan_servo.set_angle(pan_angle)
    finally:
        state.running = False
        cap.release()
        pan_servo.stop()
        tilt_servo.stop()
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
