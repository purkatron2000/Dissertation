# Jetson Nano Work Log

Date range: March 9-10, 2026

## Project Context

This log records the Jetson Nano connection setup, sensor diagnostics, fixes attempted, user actions, and the final working state reached during this round of troubleshooting.

## Device and Connection Details

- Device: Jetson Nano
- Hostname: `luke-desktop-jetson-nano`
- OS: Ubuntu 18.04.6 LTS
- Kernel: `4.9.253-tegra`
- Architecture: `aarch64`
- SSH user: `luke-dis`
- Network link: direct Ethernet from Mac to Nano
- Nano IP: `192.168.1.10`
- Mac USB Ethernet interface: `en8`
- Mac static IP used for link: `192.168.1.20/24`

## Original Hardware List

- MLX90640 thermal camera
- DFRobot URM13 SEN0352 ultrasonic sensor
- DHT22 temperature and humidity sensor
- Pi Camera V2
- Pan servo
- Tilt servo
- External 5V power feed

## User Actions and Physical Work

- User provided the original Nano memory notes and full setup log.
- User asked for a condensed persistent summary file.
- User ran the required Mac networking commands multiple times after reboots and reconnects.
- User physically plugged the Nano Ethernet link back in when it was found disconnected.
- User had a technician inspect and work on the wiring.
- User reported the camera had already worked previously.
- User clarified that one remaining sensor may be physically broken.

## Mac-Side Network Setup Used

These commands were required whenever the direct Ethernet link was lost:

```bash
sudo ifconfig en8 up 192.168.1.20 netmask 255.255.255.0
sudo sysctl -w net.inet.ip.forwarding=1
sudo pfctl -e -f /etc/pf.conf
ping -c 2 192.168.1.10
```

## Remote Access Work

- Verified `en8` state on the Mac
- Restored static IP and routing as needed
- Reconnected to the Nano over SSH
- Confirmed reachability with `ping`
- Confirmed SSH login and hostname on the Nano

## Files Created During This Work

In the local project folder:

- `jetson-nano-summary.md`
- `jetson_nano_pin_check.py`
- `run_jetson_nano_pin_check.sh`
- `JetsonPinCheck.desktop`
- `mlx90640_temp_report.py`

On the Nano desktop:

- `~/Desktop/jetson_nano_pin_check.py`
- `~/Desktop/run_jetson_nano_pin_check.sh`
- `~/Desktop/JetsonPinCheck.desktop`
- `~/Desktop/mlx90640_temp_report.py`

## Diagnostic Work Performed

### Initial Findings

- Camera device `/dev/video0` existed
- PWM devices were present on the system
- No external I2C sensors were initially visible on the user I2C buses
- DHT22 returned no readings
- Servos had PWM signal availability but no movement

### Pin and Device Diagnostic Script

A standalone Python script was written to:

- document the expected 40-pin header mapping
- scan I2C buses
- test MLX90640 presence
- test URM13 presence
- test DHT22 reads
- check camera readiness
- check PWM path presence for the servo channels

This script was copied to the Nano desktop and a clickable launcher was created for it.

### MLX90640 Thermal Sensor

Initial state:

- not visible at first during earlier checks

After wiring work:

- detected on I2C bus `1` at address `0x33`
- successfully read with a dedicated Python script

Example successful read:

- Average temperature: `25.80 C`
- Center temperature: `26.16 C`
- Minimum temperature: `21.04 C`
- Maximum temperature: `27.97 C`

Important software note:

- the installed `adafruit_mlx90640` library has a Python 3.6 compatibility issue involving `WriteableBuffer`
- a local script workaround was used successfully without changing system packages

### URM13 Ultrasonic Sensor

Initial state:

- not detected during early scans

Later state after wiring work:

- direct I2C read to address `0x12` succeeded
- sensor responded on bus `1`

Important note:

- `i2cdetect -y 1` did not always show `0x12`
- direct reads were more reliable than generic scan output for this device

### DHT22 Temperature and Humidity Sensor

- repeatedly failed to return data
- repeated test output reported `DHT sensor not found, check wiring`
- remained non-working at the end of this session
- user indicated this sensor may be broken

### Pi Camera V2

- camera had worked previously according to the user
- `/dev/video0` existed during checks
- camera support on the Nano was present
- earlier work had already confirmed frame capture through the Jetson GStreamer pipeline

### Servos

- earlier work confirmed PWM-related hardware paths and control setup
- PWM availability was checked through sysfs paths and scripts
- no final movement verification was completed in this round
- external power remained a likely dependency for physical movement

## Connectivity Interruptions Encountered

- The direct Ethernet link was not persistent across Mac restarts and reconnects
- At one point `en8` had no address
- At one point `en8` did not exist because the Ethernet adapter was unplugged
- Once the adapter was plugged back in and the static network setup was re-applied, SSH access resumed normally

## Current Final Status

Working:

- MLX90640 thermal sensor
- URM13 ultrasonic sensor
- Pi Camera V2 previously confirmed working

Partially confirmed:

- Servo PWM/control path present, but physical servo movement was not re-verified in this final pass

Not working:

- DHT22 temperature and humidity sensor

## Final Summary

Progress was made. The system moved from no external I2C sensors being visible to two working sensors being confirmed:

- `MLX90640`
- `URM13`

The `DHT22` remained non-functional and is likely broken or still miswired. The camera was previously confirmed working, and the servo control side was present in software, though physical servo movement was not the focus of the final retest.

## DHT22 Follow-Up On March 11, 2026

Additional context was provided from an email discussion indicating that the DHT22 needs to be actively polled with the correct single-wire style protocol on Jetson Nano, and that the issue might be protocol handling rather than a dead sensor.

Work completed:

- Reconnected to the Nano and re-verified SSH access
- Reviewed the NVIDIA forum guidance referenced in the email
- Confirmed that DHT22 on Jetson Nano is timing-sensitive and often requires a Jetson-specific implementation
- Retested the existing Python `adafruit_dht` path on multiple likely Jetson pins
- Cloned and built the Jetson-specific `C_DHT` implementation referenced by the NVIDIA guidance
- Reconfigured that helper away from its default Xavier assumptions
- Tested the expected DHT data pin on the Nano
- Added a raw GPIO scan mode and swept the most plausible Nano header pins directly

Pins and GPIOs tested with the Jetson-specific low-level reader:

- Board pin `7` / GPIO `216` / `AUD_MCLK`
- Board pin `11` / GPIO `50`
- Board pin `12` / GPIO `79`
- Board pin `13` / GPIO `14`
- Board pin `15` / GPIO `194`
- Board pin `16` / GPIO `232`
- Board pin `18` / GPIO `15`
- Board pin `22` / GPIO `13`
- Board pin `29` / GPIO `149`
- Board pin `31` / GPIO `200`
- Board pin `33` / GPIO `38`

Result:

- No valid DHT22 frame was returned on any tested pin
- The failure remained even when using the Jetson-specific timing-sensitive reader

Conclusion from this follow-up:

- The DHT22 issue is no longer most likely a simple software/protocol mistake
- The sensor is still not electrically reachable in a usable way from the Nano
- The most likely remaining causes are:
  - data wire on the wrong header pin
  - power or ground wiring error
  - DHT breakout/module wiring issue
  - faulty sensor or module despite earlier assumptions

Recommended physical checks to pass on:

- verify the exact Nano header pin used for the DHT data line
- verify `Vcc` and `GND` are not swapped
- verify the DHT breakout shares ground with the Jetson
- verify the breakout output pin is actually connected to the Nano data pin

## Servo Follow-Up On March 11, 2026

Further work was done to determine why the pan and tilt servos still were not moving even though software PWM control code already existed.

Conclusion:

- The main servo problem was not the Python library
- The main servo problem was not the overlay application logic
- The main servo problem was that Jetson Nano header pins `32` and `33` were not configured for PWM in Jetson-IO

Research and verification completed:

- Confirmed that Jetson Nano supports hardware PWM on:
  - pin `32` as `pwm0`
  - pin `33` as `pwm2`
- Confirmed that using hardware PWM through sysfs is a valid control path for SG90-style micro servos on Jetson Nano
- Checked Jetson-IO on the Nano and found:
  - Header 1 had no enabled functions
  - pin `32` was effectively unused
  - pin `33` was not configured as active PWM

Fix applied:

- Enabled `pwm0` and `pwm2` on Header 1 using Jetson-IO
- Saved updated boot configuration
- Rebooted the Nano to apply the pinmux change

After reboot:

- Jetson-IO reported Header 1 enabled functions:
  - `pwm0 (32)`
  - `pwm2 (33)`
- A direct servo sweep test successfully wrote valid servo pulse widths on both channels
- User confirmed the servo fix worked

Important interpretation:

- This PWM pinmux issue is separate from the DHT22 problem
- The DHT22 does not use pins `32` or `33`
- The DHT22 troubleshooting remains a separate wiring or connectivity issue

Combined camera and sensor app updates:

- The main `camera_sensor_overlay.py` app was updated to use the working servo PWM path cleanly
- The app now supports:
  - camera view
  - MLX90640 thermal overlay
  - URM13 ultrasonic distance overlay
  - pan and tilt servo control
  - arrow keys for servo motion
  - sliders for servo position
  - space bar to center both servos

Current state after this work:

- MLX90640 thermal sensor working
- URM13 ultrasonic sensor working
- Pi Camera V2 working
- Pan and tilt servo PWM path configured correctly and working after Jetson-IO fix
- DHT22 still not returning valid data

## Follow-Up Work On March 10-17, 2026

This section records the work completed after the previous log entry, including the DHT22 pin discovery, C_DHT testing, servo tuning work, camera overlay changes, and the new integrated project application.

## DHT22 Progress After Pin Discovery

Important user clarification:

- the DHT22 data line was later identified as being on board pin `31`

Mapping confirmed:

- board pin `31`
- `board.D6`
- Linux GPIO `200`

What changed after this discovery:

- the normal Python `adafruit_dht` test was rerun on `board.D6`
- the result changed from complete failure to partial-buffer style errors
- this suggested the code was now hitting the correct wire, but the signal remained unreliable

Jetson-specific low-level work:

- built and tested the `GrgoMariani/NVidia-Jetson-DHT22-Python` `C_DHT` path
- used the raw GPIO reader against Linux GPIO `200`
- repeated the read in a retry loop

Key result:

- after multiple failed attempts, a valid read was eventually obtained on pin `31`
- example successful low-level reading:
  - temperature about `16.4 C`
  - humidity about `61.3%`
  - valid flag `ok=1`

Interpretation:

- the DHT22 is not dead
- the Nano can read it
- the signal is timing-sensitive and unreliable, but not completely broken

Additional DHT utility files created:

- `~/Desktop/dht22_test.py`
  - simple `adafruit_dht` path
- `~/Desktop/dht22_test_c_dht.py`
  - Jetson-specific `C_DHT` path
- `~/Desktop/dht22_pin31_c_dht_test.py`
  - exact pin `31` / GPIO `200` low-level test
- `~/Desktop/run_c_dht_pin31.py`
  - helper script for the low-level pin `31` test
- `~/Desktop/dht22_pin31_c_dht_10min_test.py`
  - 10-minute soak test launcher
- `~/Desktop/run_c_dht_pin31_10min.py`
  - helper script that attempts a read every `5` seconds for `10` minutes and counts successful reads

Important implementation details:

- the working `C_DHT` path is still effectively Python `2.7` based
- the clean repo path on the Nano had to be rebuilt properly
- the low-level helper was moved into a cleaner non-suffixed folder path
- explicit `codex` references were removed from the user-facing launcher files

Current DHT conclusion:

- DHT22 functionality has been implemented in software
- readings remain unreliable
- the hardware is partially working, not absent
- this remains a known unstable part of the system pending further physical fixes

## Servo Tuning Work

Separate servo development work was done beyond the original overlay script.

New standalone servo-only tool created:

- `~/Desktop/servo_tuner.py`

Purpose:

- isolate servo behavior from the camera/sensor overlay
- test very small pulse-width changes
- investigate smoother movement and reduce chatter

What it implements:

- direct hardware PWM through sysfs
- separate control of both servo channels
- microsecond-based control instead of only coarse angle jumps
- per-servo min/max pulse controls
- center, hold, and relax actions

Observations:

- the initial conservative range was too narrow and limited travel
- the UI was then widened and corrected to expose the full controls properly
- later user feedback confirmed the tuner window was initially too small and had to be resized and made expandable

Important physical interpretation:

- the rig does not map the intuitive servo names cleanly
- what had been called `tilt` in some earlier code appears to drive left/right motion
- what had been called `pan` in some earlier code appears to drive up/down motion
- this axis confusion explains why some tracking direction fixes seemed backwards

## Camera Overlay Follow-Up

The older combined overlay app continued to be used for testing:

- `~/Desktop/camera_sensor_overlay.py`

Work completed:

- camera orientation was adjusted multiple times
- thermal inset was moved to the bottom-left of the frame
- the on-screen `q/Esc quit` hint was removed
- non-essential servo scan logic was reverted after it made behavior worse

Important issue encountered:

- after a simple camera orientation change, the app began crashing with:
  - `Failed to create CaptureSession`
  - `Could not read camera frame`

Root cause:

- the failure was not the orientation line itself
- `nvargus-daemon` had entered a bad state and was rejecting capture sessions

Recovery performed:

- restarted `nvargus-daemon`
- verified camera capture worked again afterward

Important lesson:

- some camera failures during this period were caused by the Jetson camera service state, not by the small orientation edits being tested

## New Main Project Application

A new separate standalone application was written to move beyond the ad hoc test scripts and follow the actual project outline more closely.

New file created:

- `~/Desktop/thermal_depth_alignment_app.py`

Bundled supporting assets copied with it:

- `~/Desktop/assets/haarcascade_frontalface_default.xml`
- `~/Desktop/assets/haarcascade_profileface.xml`

Purpose of the new application:

- RGB camera head/face detection
- pan-tilt tracking
- thermal region extraction
- URM13 distance integration
- DHT22 ambient input integration
- simple corrected face-heat estimate against a configurable normal reference

Implementation notes:

- RGB detection uses bundled OpenCV Haar cascade XML files because the Nano did not provide the expected cascade path helpers cleanly
- tracking is based on the RGB detection, not on the thermal camera
- the thermal camera is used to extract heat data from the mapped face region
- the correction model is currently heuristic, not yet trained from a calibration dataset

What the new app currently includes:

- working camera path through Jetson GStreamer
- background thread for MLX90640 thermal frames
- background thread for URM13 distance reads
- background thread for optional DHT22 reads on `board.D6`
- hardware PWM control for the two servos
- automatic tracking mode
- manual arrow-key override mode
- thermal inset display
- on-screen diagnostic overlay

## New App Tracking Calibration Changes

During live testing of the new main application, tracking direction remained incorrect.

Initial mistaken assumption:

- code assumed conventional axis naming where:
  - pan = left/right
  - tilt = up/down

User observation corrected this:

- on this actual rig:
  - the current tilt servo channel controls left/right motion
  - the current pan servo channel controls up/down motion

Temporary calibration system added:

- tracking direction can now be adjusted at runtime
- the app writes the current calibration state to:
  - `~/Desktop/tracking_direction_settings.txt`

Current calibration keys:

- `h`
  - flip horizontal tracking direction
- `v`
  - flip vertical tracking direction
- `p`
  - alias for horizontal flip
- `y`
  - alias for vertical flip
- `1`
  - horizontal-only tracking
- `2`
  - vertical-only tracking
- `3`
  - both axes tracking

Reason for this temporary code:

- make it possible to identify the correct sign and axis mapping live on the Nano
- avoid repeatedly patching the code blindly while testing
- once the correct settings are confirmed, this temporary calibration support can be removed

Current default calibration state written to file:

- `horizontal_sign=-1`
- `vertical_sign=1`
- `track_mode=horizontal`

## Current Open Problems As Of March 17, 2026

- DHT22 still reads unreliably even though valid reads have now been proven possible on pin `31`
- tracking direction still needs live confirmation using the new temporary calibration controls
- the physical servo axis naming in the rig is crossed relative to the earlier logical `pan` / `tilt` naming
- the correction model in the new integrated application is still a placeholder heuristic and will need calibration data later
- thermal-to-RGB alignment is currently based on a simple center mapping and not yet on a proper measured calibration procedure

## Current Working State As Of March 17, 2026

Working:

- Pi Camera V2
- MLX90640 thermal camera
- URM13 ultrasonic distance sensor
- servo PWM output and manual servo control
- new integrated standalone application starts and runs on the Nano desktop

Partially working:

- DHT22 low-level reads on pin `31` / `board.D6`
- auto-tracking logic present, but tracking direction still under calibration

Not yet finished:

- robust DHT22 reliability
- final correct tracking sign settings
- final calibrated thermal/RGB alignment
- final fitted temperature-correction model

## Servo Research Follow-up As Of March 18, 2026

The servo issue was revisited with a deeper software and hardware review because the servos still moved in a visibly jittery and jerky way even after PWM output had already been proven to work.

### Libraries and software paths checked

The following onboard software paths were investigated for Jetson Nano servo control:

- direct Linux hardware PWM through `/sys/class/pwm`
- official `Jetson.GPIO.PWM`

Findings:

- both of these are meaningful onboard options for this setup
- `Jetson.GPIO.PWM` is available on the Nano and works
- both approaches ultimately use the same Jetson hardware PWM path, so changing between them is not expected to materially improve smoothness
- there are no other materially different onboard Jetson servo libraries left to try for this hardware-only setup

Additional library checks:

- `adafruit_motor.servo` imports on the Nano, but it is only a helper layer and does not provide a different onboard PWM backend
- `adafruit_servokit` and `adafruit_pca9685` do not currently import cleanly on the Nano Python 3.6 environment
- those Adafruit libraries would only become relevant if an external PWM board such as a PCA9685 is added later

### Research conclusions

The best current conclusion is that the servo problem is primarily hardware / electrical / mechanical rather than a missing Python library.

Main reasons:

- SG90-style analogue micro servos are not ideal for very smooth tiny-increment motion
- analogue deadband and backlash likely limit how smoothly these servos can respond
- the observed symptom where aggressive servo movement disturbed other sensors strongly suggests shared power / ground noise is also involved
- software-only changes are unlikely to eliminate the jitter on the current setup

### Separate servo test work

A new standalone test script was created so servo experiments remain separate from the main application:

- `~/Desktop/servo_gpio_smooth_lab.py`

Purpose:

- test the official `Jetson.GPIO.PWM` path separately
- compare hold-versus-relax behavior
- tune pulse limits, step sizes, and update timing
- keep all servo experimentation isolated from the main integrated program

### Current servo status

- servo movement works
- servo direction and tracking direction in the main app were previously corrected
- smoothness is still not acceptable
- the remaining issue is now treated as a hardware-side limitation/problem rather than an unresolved software-library choice

## Thermal Depth Alignment App Status As Of March 18, 2026

The thermal depth alignment application is still the current main standalone prototype:

- `~/Desktop/thermal_depth_alignment_app.py`

Tracking state:

- tracking direction was fully calibrated and then hardcoded into the program
- temporary live calibration code was removed after confirmation
- current confirmed mapping on this rig is:
  - horizontal motion uses the current tilt servo channel
  - vertical motion uses the current pan servo channel
  - horizontal tracking sign is fixed to `-1`
  - vertical tracking sign is fixed to `1`

This means the earlier "tracking sign still under calibration" state is no longer current.

## Face Detection Upgrade: Haar Cascades to MTCNN — March 18, 2026

### Problem and motivation

The existing face detection in `thermal_depth_alignment_app.py` used OpenCV Haar cascades (frontal + profile + flipped profile). This approach has well-known limitations:

- high false-positive rate especially in cluttered backgrounds
- poor detection when the face is tilted, partially occluded, or at an angle
- no facial landmarks, meaning the thermal region targeting was based on the geometric centre of the bounding box rather than a clinically meaningful region
- the project outline calls for accurate face-region thermal extraction, and using the forehead (the area targeted by clinical IR thermometers) requires knowing where the eyes are so the forehead can be located above them

### Options considered

Four detection approaches were evaluated for the Jetson Nano's constraints (Python 3.6, 4GB RAM, CUDA 10.2, OpenCV 4.1.1 with DNN module):

1. **MTCNN (Multi-task Cascaded CNN)** — Zhang et al. 2016. Detects faces plus 5 landmarks (left eye, right eye, nose, mouth left, mouth right). Handles tilted/partial faces well. Runs ~5-8 FPS on Nano which is acceptable if detection is run every few frames with tracking coasting between. Good academic citations. Requires TensorFlow or PyTorch as backend.

2. **OpenCV DNN SSD (MobileNet)** — OpenCV's built-in DNN module with a pre-trained MobileNet-SSD face detector. Faster (~10-15 FPS) but no facial landmarks. Would be a significant upgrade from Haar but less useful for forehead targeting.

3. **NVIDIA Jetson Inference (DetectNet)** — TensorRT-optimised, best raw FPS. However, more complex setup, Jetson-specific, and harder to document/justify in a report. Overkill for face-only detection.

4. **Hybrid approach** — fast primary detector every frame + MTCNN periodically for landmark confirmation. Best accuracy but most complex to implement and maintain.

### Decision

MTCNN was chosen because:

- it provides facial landmarks which directly enable forehead-based thermal targeting, a clinically meaningful improvement
- the landmark detection means the eye/nose positions can be used to identify the exposed skin region for thermal reading, which ties directly into the project outline's goal of accurate face-region temperature estimation
- it is well-cited in academic literature (Zhang et al. 2016), which strengthens the project report
- the ~5-8 FPS on Nano is acceptable because detection can run every 3rd frame while the last detection result is used for tracking/display in between
- it handles the angles and partial occlusion cases that Haar cascades consistently fail on

### Installation challenges on Jetson Nano

The MTCNN pip package (`mtcnn` by ipazc) requires TensorFlow as its backend. Installing TensorFlow on the Jetson Nano (Python 3.6, aarch64) proved non-trivial:

1. The Nano has no internet by default — it is connected via a direct Ethernet link to the Mac. DNS resolution was failing, so the Nano's default gateway was set to the Mac (192.168.1.20) and DNS was pointed to Google (8.8.8.8). The Mac was already forwarding traffic via `net.inet.ip.forwarding=1` and `pfctl`. After this, the Nano could reach the internet.

2. NVIDIA provides pre-built TensorFlow wheels for JetPack 4.6 at their compute/redist index. The latest available version for this JetPack is `tensorflow==2.6.2+nv21.12`.

3. TensorFlow 2.6.2 depends on `h5py~=3.1.0`. Building h5py 3.1.0 from source on the Nano required numpy compilation, which failed because:
   - pip's build isolation pulled `numpy==1.12` (the Python 3.6 pin) into a temporary build environment
   - numpy 1.12 includes `#include <xlocale.h>` which does not exist on Ubuntu 18.04 (it was removed in glibc 2.26)
   - the fix was to create a symlink: `sudo ln -sf /usr/include/locale.h /usr/include/xlocale.h`

4. After the xlocale fix, TF install was retried and is currently building h5py from source (expected ~10-15 minutes on ARM).

5. Other prerequisites installed: `protobuf==3.19.6`, `keras_preprocessing==1.1.2`, `keras_applications==1.0.8`, `gast==0.4.0`, `future==0.18.2`, `mock==3.0.5`, `cython`, `setuptools`, system packages `libhdf5-serial-dev`, `hdf5-tools`, `zlib1g-dev`, `libjpeg8-dev`, `liblapack-dev`, `libblas-dev`, `gfortran`.

### Code changes made

The following changes were made to `thermal_depth_alignment_app.py`:

#### 1. MTCNN import with graceful fallback

Added a try/except import block at the top of the file. If `mtcnn` is not installed, the app falls back to the original Haar cascade path. This means the code works in both states — before and after MTCNN is installed.

#### 2. New detection constants

- `MTCNN_DETECT_INTERVAL = 3` — run MTCNN every 3rd frame to maintain usable FPS
- `MTCNN_MIN_CONFIDENCE = 0.85` — reject low-confidence detections to reduce false positives
- `MTCNN_INPUT_SCALE = 0.5` — downscale the input frame for speed

#### 3. Unified detector loading

New `load_detector()` function replaces `load_cascades()`. Returns a detector dict with `backend` field ("mtcnn" or "haar") so the rest of the code can branch appropriately. Falls back to Haar automatically if MTCNN import failed.

#### 4. MTCNN detection function

New `detect_heads_mtcnn()` function:

- downscales the BGR input frame
- converts BGR to RGB (MTCNN expects RGB)
- runs `mtcnn.detect_faces()` which returns bounding boxes, confidence scores, and 5 facial keypoints
- scales results back to original frame coordinates
- filters by `MTCNN_MIN_CONFIDENCE`
- returns detections in the same dict format as the Haar path, with additional `confidence` and `landmarks` fields

The original Haar detection was preserved as `detect_heads_haar()` and a unified `detect_heads()` dispatcher calls the right backend.

#### 5. Frame-skipping in the main loop

In `run_ui()`, MTCNN detection now runs every `MTCNN_DETECT_INTERVAL` frames. Between detection frames, the last cached result is used for tracking and display. This is the standard approach for heavier CNN detectors on embedded hardware — the tracking servo loop still runs every frame using the cached face position, so tracking responsiveness is maintained.

#### 6. Landmark-based forehead targeting

New `get_forehead_point()` function estimates the forehead centre from MTCNN landmarks. The forehead is the midpoint between the top of the bounding box and the eye line (averaged between left and right eye positions). This is the region that clinical IR thermometers target because it has consistent skin exposure and minimal interference from hair or glasses.

`extract_face_proxy()` was updated to accept an optional `landmarks` parameter. When landmarks are available, the thermal ROI is centred on the estimated forehead point instead of the geometric face centre. This should produce more clinically meaningful temperature readings.

#### 7. OSD updates

- detection label now shows MTCNN confidence percentage when available
- MTCNN facial landmarks (eyes in cyan, nose/mouth in orange) are drawn on the video feed
- status bar shows which detection backend is active ("mtcnn" or "haar")

### Files modified

- `~/Desktop/thermal_depth_alignment_app.py` — all changes above (edited locally on Mac, pending SCP deploy to Nano)

### Full installation resolution

The TF dependency chain required several additional fixes beyond the xlocale.h symlink:

1. **Cython version conflict**: h5py 3.1.0 cannot be built with Cython 3.x. The initial Cython install pulled 3.0.12 which caused `CompileError` in `h5py/_conv.pyx`. Fix: downgraded to `Cython==0.29.37` via `pip3 install --user "Cython<3"`.

2. **pip build isolation**: Even after fixing Cython, pip's default build isolation was pulling incompatible toolchain versions. Fix: used `pip3 install --user --no-build-isolation h5py==3.1.0` so h5py used the locally installed Cython and numpy.

3. **pkgconfig module**: h5py build also needed the `pkgconfig` Python module to locate system HDF5 headers. Fix: `pip3 install --user pkgconfig`.

4. **h5py built successfully** after all three fixes, producing `h5py-3.1.0-cp36-cp36m-linux_aarch64.whl`.

5. **TensorFlow wheel**: Installed with `--no-deps` since h5py was already handled: `pip3 install --user --no-deps --extra-index-url https://developer.download.nvidia.com/compute/redist/jp/v46 tensorflow==2.6.2+nv21.12`. The 317 MB pre-built wheel downloaded from NVIDIA's CDN.

6. **numpy illegal instruction crash**: The pip numpy 1.19.5 binary wheel was compiled with ARM instructions not available on the Jetson Nano's Cortex-A57. Importing it caused `Illegal instruction (core dumped)`. Fix: uninstalled the binary wheel and rebuilt from source with `pip3 install --user --no-binary numpy "numpy>=1.19.2,<1.20"`. This compiled numpy locally on the Nano (~10 minutes) producing a compatible binary.

7. **Remaining TF deps**: Installed exact versions to satisfy TF 2.6.2's requirements:
   - `keras==2.6.0`
   - `clang==5.0`, `gast==0.4.0`, `keras-preprocessing==1.1.2`
   - `flatbuffers~=1.12.0`, `typing-extensions~=3.7.4`, `absl-py==0.12.0`
   - `tensorflow-estimator==2.6.0`, `tensorboard==2.6.0`
   - `wrapt~=1.12.1`, `six~=1.15.0`, `wheel~=0.35`
   - Various transitive deps (google-auth, grpcio, etc.)

8. **MTCNN installed**: `pip3 install --user --no-deps mtcnn` installed mtcnn 0.1.1 cleanly.

9. **Verification**: `python3 -c "from mtcnn import MTCNN; print('MTCNN OK')"` succeeded.

### Deployment and test result

The updated `thermal_depth_alignment_app.py` was SCP'd to the Nano and tested with `--test-once`:

```
detector: mtcnn
detector_errors: ok
camera: True
detections: 0
distance_cm: 55
servo: {'pan_ready': True, 'tilt_ready': True, 'pan_error': None, 'tilt_error': None}
thermal: {'avg_c': 24.22, 'center_c': 26.49, 'min_c': 18.43, 'max_c': 31.49}
```

Key observations:

- MTCNN loaded as the detection backend successfully
- TensorFlow detected and used the Jetson's NVIDIA Tegra X1 GPU (compute capability 5.3, 272 MB GPU memory allocated)
- TF loaded cuDNN 8.2.1 for inference
- all sensors responded normally
- `detections: 0` was expected as no person was in front of the camera during the test
- initial TF/MTCNN model load takes ~10 seconds on the Nano (first inference warmup), subsequent detections will be faster

### Known issue: typing-extensions conflict

Installing `typing-extensions~=3.7.4` (required by TF 2.6.2) conflicts with `adafruit-circuitpython-typing` which wants `typing-extensions~=4.0`. This does not cause a runtime error because the Adafruit typing module falls back gracefully. If this becomes a problem later, the typing-extensions version may need to be bumped and TF's complaint ignored.

### Current status after MTCNN deployment

Working:

- Pi Camera V2
- MLX90640 thermal camera
- URM13 ultrasonic distance sensor
- servo PWM control
- MTCNN face detection with TensorFlow 2.6.2 + CUDA GPU acceleration
- forehead-targeted thermal region extraction (when MTCNN landmarks are available)
- Haar cascade fallback if MTCNN is unavailable

Partially working:

- DHT22 on pin 31 / board.D6 (unchanged — still unreliable)

Not yet tested live:

- MTCNN detection performance with a live face in the camera
- forehead thermal targeting accuracy vs the old face-centre approach
- auto-tracking responsiveness with MTCNN's frame-skip cadence

## MTCNN Performance Failure and Switch to OpenCV DNN SSD — March 18, 2026

### The performance problem

After the MTCNN deployment was verified working via `--test-once`, the user ran the full live UI on the Nano desktop. The result was approximately **0.5 FPS** (about 1 frame every 2 seconds). This made the application completely unusable — the camera feed was a slideshow, tracking was impossible, and the UI was unresponsive.

### Why MTCNN was so slow

The root cause was TensorFlow's memory and compute overhead on the Jetson Nano's constrained hardware:

- The Nano has only 4GB of **shared** RAM between CPU and GPU. TensorFlow allocated 272MB of GPU memory just for its runtime, plus substantial CPU-side memory for the Python process, model weights, and intermediate tensors.
- MTCNN is a three-stage cascaded neural network (P-Net, R-Net, O-Net). Each stage runs a separate forward pass through TensorFlow. On the Nano's 128-core Maxwell GPU with only compute capability 5.3, each forward pass was slow.
- TensorFlow itself has a heavy Python-side overhead on each inference call — session management, tensor allocation, data copying between CPU and GPU memory. On a device where every MB matters, this overhead dominated.
- The initial model load took ~10 seconds (TF runtime initialization + cuDNN library loading + model weight loading + first inference JIT compilation). Even after warmup, each subsequent detection took ~1.5-2 seconds per frame.
- Even though MTCNN was only running every 6th frame (after the first tuning pass), the TF inference on the detection frames was so slow it blocked the entire main loop, dragging overall FPS down to ~0.5.

### First tuning attempt

Before abandoning MTCNN, the following optimizations were tried:

1. **TF GPU memory limit**: Added `tf.config.experimental.set_virtual_device_configuration` to cap TF's GPU allocation at 150MB (down from the default unlimited). Reasoning: leave more GPU memory free for OpenCV's camera pipeline and display rendering.

2. **Input scale reduction**: Changed `MTCNN_INPUT_SCALE` from `0.5` to `0.25`. This means MTCNN processes a frame that is 1/16th the area of the original (160x120 instead of 320x240 from a 640x480 source). Reasoning: the three MTCNN stages are resolution-dependent — smaller input means dramatically fewer operations per forward pass.

3. **Detection interval increase**: Changed `MTCNN_DETECT_INTERVAL` from `3` to `6`. This means MTCNN only runs on every 6th frame, with the last detection result cached and reused for the 5 frames in between. Reasoning: even if MTCNN takes 1.5 seconds, only 1 in 6 frames pays that cost — the other 5 frames should be fast (just camera read + display).

4. **TF log suppression**: Set `TF_CPP_MIN_LOG_LEVEL=3` to stop TF from printing NUMA warnings and other messages that cluttered the console.

5. **FPS counter added**: Added a real-time FPS display (yellow text, line 10 of the OSD) so the actual performance could be measured objectively instead of guessed.

### Result of tuning

After deploying all four tuning changes and running the live UI, the user reported performance was still approximately **1 frame every 2 seconds** (~0.5 FPS). The tuning had minimal effect because:

- Even at 0.25 scale, the three-stage MTCNN pipeline through TF was still ~1.5 seconds per detection
- TF's runtime overhead (memory management, kernel dispatch, GPU sync) was the bottleneck, not the model's FLOPs
- The frame-skip interval didn't help because the detection frame still blocked the main loop synchronously — during the ~1.5s MTCNN was running, no frames were displayed at all
- The GPU memory cap may have actually made things worse by forcing TF to swap more aggressively

### Decision to switch to OpenCV DNN SSD

Given that MTCNN tuning was insufficient, the decision was made to replace MTCNN entirely with OpenCV's built-in DNN face detector (ResNet-10 SSD). The reasoning:

1. **Speed**: OpenCV DNN runs entirely within OpenCV's C++ backend — no Python-side TF overhead, no GPU memory management layer, no session management. On the Nano, this is expected to hit 15-20+ FPS.

2. **Memory**: The Caffe model is ~11MB loaded. No TensorFlow means no ~1.5GB TF runtime footprint. The Nano's RAM is freed for the camera pipeline and sensor threads.

3. **Zero new dependencies**: OpenCV 4.1.1 with DNN module is already installed on the Nano. The only additional files needed are the model weights (11MB `.caffemodel`) and network definition (28KB `.prototxt`), both from OpenCV's official repository.

4. **Accuracy**: ResNet-10 SSD is substantially more accurate than Haar cascades for face detection. It handles varying angles, lighting, and partial occlusion far better. While it doesn't provide facial landmarks like MTCNN, it is a major upgrade from the original Haar cascade approach.

5. **Trade-off accepted**: Losing MTCNN's facial landmarks means the forehead targeting falls back to geometric estimation from the bounding box top (the forehead is approximately the top 30% of the face bounding box). This is less precise than eye-line-based forehead estimation but still better than the old face-centre approach, and the speed gain makes the application actually usable.

### Code changes for DNN SSD

#### Removed

- TensorFlow GPU memory configuration block
- MTCNN import with fallback
- `MTCNN_DETECT_INTERVAL`, `MTCNN_MIN_CONFIDENCE`, `MTCNN_INPUT_SCALE` constants
- `detect_heads_mtcnn()` function
- Frame-skipping logic in `run_ui()` (cached_detections, frame_count)
- All MTCNN-specific code paths

#### Added

- DNN model path constants (`_DNN_PROTO`, `_DNN_MODEL`) pointing to `~/Desktop/assets/deploy.prototxt` and `~/Desktop/assets/res10_300x300_ssd_iter_140000.caffemodel`
- `DNN_CONFIDENCE_THRESHOLD = 0.6` — minimum confidence to accept a detection
- `detect_heads_dnn()` function — creates a 300x300 blob from the input frame, runs it through the network, filters by confidence and minimum face size, returns detections in the same dict format as other backends
- Updated `load_detector()` to try DNN SSD first, fall back to Haar if model files are missing
- Updated `detect_heads()` dispatcher to route to `detect_heads_dnn()`
- Detection now runs **every frame** (no skip interval needed since DNN SSD is fast enough)
- FPS counter retained for ongoing performance monitoring

#### Preserved

- `detect_heads_haar()` as fallback
- `get_forehead_point()` and landmark-based forehead targeting (will use landmarks when available, falls back to bbox geometry)
- All sensor, servo, tracking, thermal extraction, and correction code unchanged
- Same detection result dict format (`rect`, `kind`, `confidence`, `landmarks`)
- Haar cascade asset files still present in `~/Desktop/assets/`

### Model files deployed

Downloaded to `~/Desktop/assets/` on the Nano:

- `deploy.prototxt` (28KB) — network architecture definition
- `res10_300x300_ssd_iter_140000.caffemodel` (11MB) — pre-trained weights

Source: OpenCV's official GitHub repository (`opencv/samples/dnn/face_detector/` and `opencv_3rdparty/dnn_samples_face_detector_20170830/`)

### Test result

`--test-once` output after switching to DNN SSD:

```
detector: dnn_ssd
detector_errors: ok
camera: True
detections: 0
distance_cm: 157
thermal: {'avg_c': 20.52, 'center_c': 19.73, 'min_c': 18.56, 'max_c': 31.08}
servo: {'pan_ready': True, 'tilt_ready': True}
```

Key difference from the MTCNN test: **no TF startup messages**, no GPU memory allocation messages, no cuDNN loading. The test completed in ~5 seconds total instead of ~15 seconds with MTCNN/TF. The detector loaded and ran a detection pass with negligible overhead.

### Lessons learned

1. **TensorFlow is not viable on a 4GB Jetson Nano for real-time applications** where the camera pipeline also needs shared GPU/memory resources. TF's runtime overhead alone consumes too much of the available budget.

2. **OpenCV DNN is the right abstraction layer for lightweight CNN inference on constrained devices**. It uses the same Caffe/ONNX model weights but runs through a much thinner C++ inference path with no Python-side framework overhead.

3. **MTCNN's three-stage architecture is inherently heavier than a single-shot detector** (SSD). Even with aggressive input downscaling, three sequential forward passes through TF will be slower than one forward pass through OpenCV DNN.

4. **Frame-skipping doesn't help when detection is synchronous and blocking**. To make MTCNN viable on the Nano, detection would need to run in a background thread with the main loop consuming results asynchronously. This was considered but abandoned because the DNN SSD approach is simpler and faster.

5. **The MTCNN/TF installation work was not wasted** — it proved that MTCNN is functionally correct on the Nano (correct detections, landmarks, confidence scores) and could be revisited if the Nano is upgraded to a Jetson Xavier NX or Orin with more memory and GPU cores.

### Current status

- DNN SSD deployed and verified via test-once
- Live UI performance not yet tested but expected to be 15-20+ FPS based on the model's lightweight inference profile
- TensorFlow and MTCNN packages remain installed on the Nano but are no longer imported by the application
- Forehead targeting will fall back to bounding-box geometry since DNN SSD does not provide facial landmarks

## DNN SSD Live Performance and Tracking Tuning — March 18, 2026

### DNN SSD live FPS result

After deploying DNN SSD with synchronous (inline) detection — running `detect_heads_dnn()` every frame in the main loop — the user reported **2-4 FPS**. This was a significant improvement over MTCNN's 0.5 FPS but still too slow for a smooth camera feed and responsive tracking.

### Root cause analysis

OpenCV 4.1.1 on this Nano was built **without CUDA DNN support**. This was confirmed by checking `cv2.getBuildInformation()` — the CUDA section was completely absent from the build info. This means `cv2.dnn.readNetFromCaffe()` runs the ResNet-10 SSD entirely on the Nano's ARM CPU (4x Cortex-A57 cores), not on the 128-core Maxwell GPU.

On CPU, the DNN forward pass for a 300x300 blob takes approximately 250-400ms per frame. Since detection was running synchronously in the main loop, every frame paid this cost, dragging overall FPS down to 2-4.

The GPU could have run this inference much faster (~50-80ms) but would require rebuilding OpenCV from source with `-D WITH_CUDA=ON -D OPENCV_DNN_CUDA=ON`. This was not attempted because:

- OpenCV source builds on the Nano take 2-4 hours
- the build might fail due to the constrained 4GB RAM
- there's a simpler architectural fix available

### Fix: threaded detection

Instead of running detection synchronously in the main loop, detection was moved to a dedicated background thread (`detection_worker`). The architecture:

1. **Main loop** (runs at camera speed, ~25-30 FPS):
   - reads camera frame
   - posts a copy to `state.detect_frame` for the detection thread
   - reads the latest detection result from `state.detections`
   - draws bounding boxes, runs tracking, renders overlay, displays frame

2. **Detection thread** (runs at DNN speed, ~3-5 FPS):
   - continuously grabs the latest frame from `state.detect_frame`
   - skips to the newest frame ID (never processes stale queued frames)
   - runs `detect_heads()` on that frame
   - posts results to `state.detections`
   - tracks its own FPS separately

This decouples display FPS from detection FPS. The camera feed is smooth because frame reading and display are no longer blocked by the DNN forward pass. Detection results arrive asynchronously every ~250-400ms, and the main loop uses whatever the latest result is.

### Implementation details

New `detection_worker()` function added (similar pattern to `thermal_worker`, `distance_worker`, `ambient_worker` — all existing sensor threads use the same shared-state architecture).

New fields in `SharedState`:
- `detect_frame` — latest BGR frame for the detector to process
- `detect_frame_id` — monotonic counter incremented each time a new frame is posted; the detection thread compares this to avoid reprocessing the same frame
- `detections` — latest detection result list
- `detect_fps` — detection thread's measured FPS

The main loop was updated to post `frame.copy()` under the lock and read `state.detections` in the same lock acquisition as the sensor reads (one lock per frame, minimal contention).

FPS display was updated to show two numbers: `Display: XX.X FPS | Detect: XX.X FPS` so both pipelines can be monitored independently.

### Tracking oscillation problem

After deploying threaded detection, the user reported a new problem: the tracking was **overshooting and oscillating**. The servo would chase the face, overshoot past centre, see the error in the opposite direction, correct back, overshoot again — ping-ponging endlessly.

### Root cause of oscillation

The oscillation was caused by the combination of:

1. **Stale detections**: The detection thread runs at ~3-5 FPS while the display loop runs at ~25 FPS. By the time a detection result reaches the tracking code, the face has already moved (or the servo has already started correcting). The tracking acts on where the face *was* 200-400ms ago, not where it *is* now.

2. **Aggressive integer stepping**: The original `choose_tracking_step()` function used integer division against the deadband to compute a step of 1, 2, or 3 degrees. With a deadband of 45px and a max step of 3 degrees, any error >90px (which is common when the face is near the frame edge) immediately commanded a 3-degree jump. Combined with stale detections, this guaranteed overshoot.

3. **No damping**: The step was a fixed function of error magnitude with no concept of how quickly the error was changing or how stale the detection was. A system that moves 3 degrees every 120ms (the old `TRACK_UPDATE_S`) will overshoot a target that's only 5 degrees away in under 200ms.

### First tracking tuning attempt — too passive

The first fix attempted was conservative:

- Deadband widened: X from 45 to 55, Y from 35 to 45
- Max step reduced: 3 to 2 degrees
- Gain added: `TRACK_GAIN = 0.3` — proportional correction instead of integer stepping
- Update interval slowed: 0.12s to 0.15s
- `choose_tracking_step()` rewritten to be proportional: `step = excess * TRACK_GAIN / deadband`, clamped to `[0.3, TRACK_MAX_STEP]`
- `clamp_angle()` changed from `int` to `float` to allow sub-degree servo positions

Result: **oscillation was eliminated** but the tracking became too passive. The user reported that the servo would **lose track when moving to the edge of the frame** — the correction was too small and slow to keep up with lateral movement.

The problem was that the wide deadband (55px) combined with low gain (0.3) and low max step (2 degrees) meant the tracker barely responded to moderate errors. A face at the edge of a 480px-wide frame (~200px from centre) only commanded ~1.2 degrees of correction per update, which was far too slow to follow real movement.

### Second tracking tuning attempt — balanced

The parameters were adjusted to a middle ground:

- Deadband tightened back: X to 35, Y to 25
- Max step increased: 4 degrees
- Gain increased: 0.5
- Update interval quickened: 0.10s
- Minimum step raised: 0.5 degrees (from 0.3)

Result: **much better**. The tracker was responsive enough to follow movement to the edges while the proportional gain still prevented the hard oscillation seen with the original integer stepping. However, a new problem appeared.

### Close-up hunting problem

When the user moved close to the camera, the face filled a large portion of the frame. Even though the face was visually well-centred, the face-centre pixel was many pixels away from frame-centre simply because the face was so large. The fixed 35px deadband was too small relative to the face size, so the tracker constantly tried to micro-adjust, causing the servos to twitch continuously.

This is the core issue with fixed deadbands on SG90 servos: the servos have mechanical backlash and a minimum effective step size. When commanded to make tiny corrections, they vibrate/chatter without actually moving smoothly. The tracking system was issuing corrections faster than the servos could settle, creating a constant buzz.

The user identified this themselves and suggested scaling the deadband with face size — a perceptive observation that the acceptable "close enough to centre" zone should grow proportionally with how much of the frame the face occupies.

### Adaptive deadband implementation

The fix was to make the deadband proportional to the detected face bounding box size:

```python
TRACK_DEADBAND_MIN_X = 35      # minimum deadband (used for small/distant faces)
TRACK_DEADBAND_MIN_Y = 25
TRACK_DEADBAND_FACE_RATIO = 0.4  # deadband = face_size * this ratio
```

In the tracking code:
```python
deadband_x = max(TRACK_DEADBAND_MIN_X, int(w * TRACK_DEADBAND_FACE_RATIO))
deadband_y = max(TRACK_DEADBAND_MIN_Y, int(h * TRACK_DEADBAND_FACE_RATIO))
```

How this works at different distances:

- **Far away** (face ~80px wide): `deadband_x = max(35, 80 * 0.4) = max(35, 32) = 35px`. The minimum deadband applies, so tracking is responsive — same as before.
- **Medium distance** (face ~150px wide): `deadband_x = max(35, 150 * 0.4) = max(35, 60) = 60px`. Deadband is wider, more forgiving — small offsets are accepted.
- **Close up** (face ~250px wide): `deadband_x = max(35, 250 * 0.4) = max(35, 100) = 100px`. Very wide deadband — the face needs to be 100px off-centre before the servo moves at all. This eliminates the hunting/twitching completely.

The 0.4 ratio was chosen because it means the face-centre must be displaced by more than 40% of the face width before tracking activates. Since a well-framed face has roughly equal space on each side, this translates to the face being noticeably off-centre before correction begins.

### Result

The user confirmed the adaptive deadband **helped a lot**. The servos settle when the subject is close and still, but still follow movement at a distance.

### Lessons learned

1. **Threaded detection is essential on the Nano** for any CNN-based detector without CUDA DNN support. Synchronous detection in the main loop is only viable if inference takes <30ms per frame.

2. **Tracking parameters must account for detection latency**. With 200-400ms stale detections, proportional control with moderate gain works better than aggressive integer stepping.

3. **Fixed deadbands don't work across distance ranges**. A deadband that's right for a distant face will cause hunting on a close face because pixel error scales with face size. Adaptive deadband proportional to face bounding box size is a simple and effective solution.

4. **SG90 servo mechanical limitations drive tracking design**. The servos' backlash and minimum effective step mean the software must avoid commanding corrections that are smaller than what the servo can actually execute. The adaptive deadband accomplishes this by simply not commanding corrections when the error is within the servo's effective resolution at that face size.

5. **Two-FPS-number display was valuable for debugging**. Showing both display FPS and detection FPS separately made it immediately clear that the DNN was the bottleneck, not the camera pipeline.

### Current tracking parameters

```python
TRACK_UPDATE_S = 0.10
TRACK_DEADBAND_MIN_X = 35
TRACK_DEADBAND_MIN_Y = 25
TRACK_DEADBAND_FACE_RATIO = 0.4
TRACK_MAX_STEP = 4
TRACK_GAIN = 0.5
```

### Current overall status as of end of March 18, 2026

Working:

- Pi Camera V2
- MLX90640 thermal camera
- URM13 ultrasonic distance sensor
- servo PWM control
- OpenCV DNN SSD face detection (threaded, ~3-5 FPS detection, ~25 FPS display)
- proportional auto-tracking with adaptive deadband
- forehead-targeted thermal region extraction (bbox geometry fallback)
- Haar cascade fallback if DNN model files are missing
- FPS monitoring (display + detection shown separately)

Partially working:

- DHT22 on pin 31 / board.D6 (unchanged — still unreliable)

Not yet done:

- calibrated thermal/RGB alignment
- fitted temperature correction model (still heuristic)
- live verification of forehead thermal targeting accuracy

## Servo Relax System — March 18, 2026

### Problem: weight-induced horizontal servo oscillation

Even after the adaptive deadband eliminated the close-up hunting problem, the user reported that the horizontal (tilt) servo would sometimes oscillate back and forth endlessly when the subject was stationary. The servo would hold position, the rig's weight would pull it slightly off, the tracker would see an error and correct, the correction would overshoot by a tiny amount, and the cycle would repeat.

### Root cause

The SG90 servo continuously holds position by maintaining its PWM signal. On this rig, the camera assembly is heavy enough that gravity/inertia constantly pulls the servo slightly off its commanded angle. The servo fights back, causing vibration. This is not a tracking algorithm problem — it's a physical servo holding torque problem. The servo's internal control loop is fighting the rig's weight, and the resolution of the SG90 is too coarse to settle at exactly the right angle under load.

### Why this only affects the horizontal servo

The horizontal servo (tilt channel) carries the weight of the entire camera assembly laterally. The vertical servo (pan channel) works against gravity differently — it primarily fights the camera's forward weight, which sits more aligned with the servo's rest position. The horizontal servo has to hold the rig against a sideways moment arm, which is where the SG90's limited holding torque shows as visible wobble.

### Solution: servo relax after settle

The fix is to disable PWM output (`enable=0` in sysfs) once the servo has reached its target and stayed within the deadband for a configurable delay. With PWM disabled, the servo stops actively holding position — it just sits wherever friction and gravity leave it. Since the servo was already at the right position when PWM was disabled, the rig stays put (the SG90 has enough static friction to hold position without power, it's only the active holding that causes wobble).

When a new tracking correction is needed, PWM is re-enabled immediately before the angle command, so the servo wakes up and moves to the new position.

### Implementation

Added to `HardwarePWMServo` class:

- `self.relaxed` flag — tracks whether PWM is currently disabled
- `relax()` method — writes `enable=0` to sysfs, sets `relaxed=True`
- `wake()` method — writes `enable=1` to sysfs, sets `relaxed=False`, re-sends last angle so the servo immediately goes to the correct position on wake
- `set_angle()` was updated to auto-call `wake()` if the servo is relaxed — the caller doesn't need to know about the relax state

New constant:
```python
SERVO_RELAX_DELAY = 0.5  # seconds of no correction before PWM disabled
```

In the tracking loop, each servo tracks its own `last_move_time`. If no correction has been needed for `SERVO_RELAX_DELAY` seconds, `servo.relax()` is called. Both the horizontal and vertical servos get this treatment.

### Why 0.5 seconds

The delay needs to be long enough that the servo has physically settled at its target angle before PWM is cut. If PWM is cut while the servo is still in motion, it'll stop wherever it is rather than reaching the target. 0.5 seconds is conservative — SG90 servos typically settle within 200-300ms for small movements. The extra margin accounts for the rig's inertia.

The delay also needs to be short enough that the servo doesn't visibly wobble for too long after arriving at its target. 0.5 seconds means at most half a second of hold-wobble before the servo relaxes.

### Result

Both servos now relax when the subject is stationary and within the deadband. The constant low-amplitude oscillation is eliminated. The servos wake instantly when tracking resumes.

## BME280 Sensor Discovery and Integration — March 19, 2026

### Background and motivation

The DHT22 temperature/humidity sensor had been a persistent problem throughout the project. Despite eventually getting it to return valid readings on pin 31 / GPIO 200 using the Jetson-specific C_DHT native library, the sensor remained unreliable:

- It required a timing-sensitive single-wire protocol that the Jetson Nano's non-real-time Linux kernel handles poorly
- The `adafruit_dht` Python library frequently returned `None` or raised checksum errors
- The C_DHT low-level path required building a separate C extension, running through Python 2.7 compatibility layers, and still only succeeded on a fraction of attempts
- The 10-minute soak test showed a low success rate
- Each read attempt takes 2+ seconds with retries, which is slow for a real-time application

The project needs reliable ambient temperature and humidity for the temperature correction model. The DHT22's unreliability was a blocking issue for that model's accuracy.

### Sensor discovery

The user reported that a new I2C temperature and humidity sensor had been physically installed on the rig. The task was to find it, identify it, test it, and integrate it.

### Discovery process

**I2C bus scan**: `i2cdetect -y 1` showed only `0x33` (MLX90640, already known). This was expected — `i2cdetect` uses SMBus Quick Write probes which some sensors don't respond to (the URM13 at `0x12` also doesn't show in scans but responds to direct reads).

**Direct address probing**: Common I2C temperature/humidity sensor addresses were probed with direct byte reads:

| Address | Sensor type | Bus 1 result | Bus 0 result |
|---------|------------|-------------|-------------|
| 0x38 | AHT10/AHT20 | no response | no response |
| 0x39 | AHT10 alt | no response | no response |
| 0x40 | HTU21D/Si7021/HDC1080 | no response | no response |
| 0x41 | HDC1080 alt | no response | no response |
| 0x44 | SHT30/SHT31/SHT40 | no response | no response |
| 0x45 | SHT30/SHT31 alt | no response | no response |
| 0x70 | SHTC3 | no response | no response |
| 0x76 | BME280/BMP280 | **responded: 0x00** | no response |
| 0x77 | BME280/BMP280 alt | no response | no response |
| 0x12 | URM13 (existing) | responded: 0x12 | no response |

The sensor at `0x76` on bus 1 responded.

**Chip ID verification**: The BME280/BMP280 family stores a chip ID at register `0xD0`. Reading `i2cget -y 1 0x76 0xD0 b` returned `0x60`, which is the BME280 chip ID. (BMP280 would return `0x58`; BMP180 would return `0x55`.)

**Conclusion**: The new sensor is a **Bosch BME280** — a combined temperature, humidity, and barometric pressure sensor. This is a significant upgrade over the DHT22 because:

- It communicates over I2C (reliable digital protocol, shared bus with MLX90640 and URM13)
- Reads are fast (~50ms forced mode) and don't require timing-sensitive bit-banging
- It provides pressure in addition to temperature and humidity
- No special C extensions or Jetson-specific workarounds needed
- The `smbus2` library (already installed on the Nano) is all that's needed

### Test script

A standalone test script was written at `~/Desktop/bme280_test.py` that:

1. Verifies chip ID at `0x76`
2. Reads all factory calibration constants from registers `0x88-0xA1` and `0xE1-0xE7`
3. Triggers a forced-mode measurement (humidity x1, temperature x1, pressure x1)
4. Applies the BME280 compensation formulas from the Bosch datasheet (section 4.2) to convert raw ADC values to physical units
5. Reports temperature (C), humidity (%RH), and pressure (hPa)
6. Runs sanity checks on the values

**Test result**:
```
Temperature:  19.52 C
Humidity:     37.31 %RH
Pressure:     1019.37 hPa
STATUS: OK — all three readings obtained
```

All values are within normal indoor ranges. The pressure reading (~1019 hPa) is consistent with sea-level atmospheric pressure, confirming the sensor is working correctly.

### Integration into main application

The BME280 was integrated into `thermal_depth_alignment_app.py` as the **primary** ambient sensor, with the DHT22 retained as a fallback in case the BME280 is ever removed or fails.

#### Architecture decision: BME280-first with DHT22 fallback

The `ambient_worker()` thread now tries BME280 first. If the BME280 is found (chip ID `0x60` at address `0x76` on bus 1), it enters a simple read loop with `BME280_READ_INTERVAL_S = 2.0` second intervals. No retries are needed because I2C reads are reliable.

If BME280 initialisation fails (sensor not present, wrong chip ID, I2C error), the worker falls back to the original DHT22 path with its retry logic.

This is better than making the two sensors equal-priority alternatives because:

- The BME280 is categorically more reliable than the DHT22 on this platform
- Having a clear primary/fallback hierarchy means the display can show which source is active
- The DHT22 fallback is preserved only because it was part of the original project hardware list and may still be wired up

#### Changes to SharedState

New fields:
- `ambient_pressure_hpa` — barometric pressure (BME280 only; `None` if using DHT22 fallback)
- `ambient_source` — string `"BME280"` or `"DHT22"` indicating which sensor is active

#### Changes to ambient display

The on-screen status line now shows:
- Temperature, humidity, pressure (if available), source sensor, and age
- Example: `Ambient: 19.3 C / 38.1% RH / 1019 hPa [BME280] (age 1s)`
- If BME280 is unavailable and DHT22 is active: `Ambient: 16.4 C / 61.3% RH [DHT22] (age 5s)`
- If neither is working: `Ambient: <error message>`

#### Changes to run_once diagnostic

The `--test-once` mode now tries BME280 first and reports the source:
```
ambient: {'temp_c': 19.32, 'humidity': 38.06, 'pressure_hpa': 1019.34, 'source': 'BME280'}
```

#### BME280 implementation details

The BME280 compensation formulas were implemented directly in the application (functions `_bme280_read_calibration`, `_bme280_compensate`, `_bme280_single_read`) rather than using an external pip package. This was done because:

- The only dependency is `smbus2` which was already installed
- The compensation math is well-documented in the Bosch datasheet and straightforward to implement
- Avoiding an extra pip package on the Nano's Python 3.6 environment prevents potential version/compatibility issues
- The same implementation was already tested and verified in the standalone `bme280_test.py`

The sensor operates in forced mode (one measurement per request) rather than continuous mode. This gives explicit control over timing and ensures each reading is fresh.

### Verification

`python3 thermal_depth_alignment_app.py --test-once` confirmed:
- BME280 reads correctly: 19.32 C / 38.06% RH / 1019.34 hPa
- Source correctly identified as `BME280`
- Camera, detector, thermal, distance, and servos all still working
- No regressions from the integration

### Significance for the project

The BME280 resolves the longest-standing hardware issue in the project. The ambient temperature and humidity values are now reliable enough to be used meaningfully in the temperature correction model. Previously, the correction model's ambient adjustment terms were effectively dead code because the DHT22 rarely provided valid readings. With the BME280, those adjustments will apply consistently.

The pressure reading is a bonus — while not currently used in the correction model, barometric pressure affects thermal camera readings at a second-order level and could be incorporated into a more sophisticated model later.

### Current sensor status as of March 19, 2026

| Sensor | Status | Bus/Interface | Address | Notes |
|--------|--------|--------------|---------|-------|
| Pi Camera V2 | Working | CSI | — | via GStreamer pipeline |
| MLX90640 | Working | I2C bus 1 | 0x33 | thermal camera |
| URM13 | Working | I2C bus 1 | 0x12 | ultrasonic distance |
| BME280 | Working | I2C bus 1 | 0x76 | temp/humidity/pressure (NEW) |
| DHT22 | Unreliable (fallback only) | GPIO pin 31 | — | retained as fallback |
| Pan servo | Working | PWM pin 33 | — | with relax system |
| Tilt servo | Working | PWM pin 32 | — | with relax system |

### Current overall status as of March 19, 2026

Working:

- Pi Camera V2
- MLX90640 thermal camera
- URM13 ultrasonic distance sensor
- BME280 ambient temperature/humidity/pressure sensor (new, reliable, replaces DHT22)
- servo PWM control with relax system
- OpenCV DNN SSD face detection (threaded, ~3-5 FPS detection, ~25 FPS display)
- proportional auto-tracking with adaptive deadband
- forehead-targeted thermal region extraction (bbox geometry fallback)
- Haar cascade fallback if DNN model files are missing
- FPS monitoring (display + detection shown separately)

Partially working:

- DHT22 on pin 31 / board.D6 (retained as fallback, still unreliable)

Not yet done:

- calibrated thermal/RGB alignment
- fitted temperature correction model (still heuristic, but now has reliable ambient input)
- live verification of forehead thermal targeting accuracy

## Sidebar UI Refactor — March 19, 2026

### Problem: cluttered camera view

All sensor data, tracking state, correction output, and FPS counters were rendered as text directly on top of the 640x480 camera frame using `add_status_line()`. This drew 11 lines of text over the camera feed, obscuring the subject — especially problematic when trying to visually assess face detection accuracy, thermal region targeting, or tracking behaviour. The user requested that sensor information be moved to a separate panel while keeping the thermal camera inset on the main camera view.

### Design decision: sidebar panel vs second window

Two approaches were considered:

1. **Separate OpenCV window** — `cv2.namedWindow("Sensors")` and a second `cv2.imshow()` call. This would create two independent windows that the user would need to position and manage. On the Nano's desktop this is workable but awkward — windows overlap, can't be docked, and require manual arrangement.

2. **Composite frame with sidebar** — concatenate the camera frame with a dark sidebar panel using `np.hstack()` and display as a single wider window. This keeps everything in one window, automatically positioned, and trivial to implement with numpy.

Option 2 was chosen because:
- single window is simpler to manage on the Nano desktop
- no extra window positioning code needed
- the composite is just one `np.hstack()` call — minimal CPU overhead
- the total window size (940x480) fits comfortably on the Nano's display

### Implementation

#### New constants

```python
SIDEBAR_WIDTH = 300
SIDEBAR_BG = (30, 30, 30)       # dark grey background
SIDEBAR_FONT = cv2.FONT_HERSHEY_SIMPLEX
SIDEBAR_FONT_SCALE = 0.48
SIDEBAR_LINE_HEIGHT = 20
SIDEBAR_MARGIN_X = 10
SIDEBAR_MARGIN_Y = 20
```

The font scale was reduced from 0.72 (used by the old `add_status_line`) to 0.48 to fit text within the 300px sidebar width. Line height was reduced from 28px to 20px to fit more information. The dark grey background (30, 30, 30) provides contrast without being pure black, which visually separates it from the camera feed.

#### New functions

- `_sidebar_text(sidebar, row, text, color)` — draws a single text line at the calculated position
- `_sidebar_heading(sidebar, row, text)` — draws a section heading (slightly brighter and larger)
- `draw_sidebar(sidebar, info)` — master function that takes a dict of all state values and renders the complete sidebar

#### Sidebar layout

The sidebar is organised into logical sections with headings:

```
TRACKING
  Mode: Auto-Track
  Horizontal: 90.0 deg
  Vertical: 90.0 deg
  Detector: dnn_ssd

DETECTION
  Faces: 1
  Raw proxy: 32.45 C
  Corrected: 33.12 C
  vs Normal: -0.88 C
  within expected normal proxy range

SENSORS
  Distance: 46 cm
  Temp: 19.3 C  Hum: 38.1%
  Pres: 1019 hPa  [BME280] (1s ago)
  Thermal avg: 22.5 C
  Thermal min: 16.2  max: 32.0 C

PERFORMANCE
  Display: 25.3 FPS
  Detect:  3.8 FPS

  t:track  space:center
  arrows:manual  q:quit
```

This grouping makes it easy to scan for the information you need — tracking state at the top, then detection/correction results, then raw sensor values, then performance metrics, then keyboard help at the bottom.

#### Main loop changes

The 11 `add_status_line()` calls in the main loop were replaced with:

1. Thermal inset still drawn directly on the camera frame (unchanged)
2. A new `sidebar` numpy array created each frame: `np.full((rgb_h, SIDEBAR_WIDTH, 3), SIDEBAR_BG, dtype=np.uint8)`
3. `draw_sidebar(sidebar, {...})` called with a dict containing all current state
4. Composite created: `display = np.hstack([frame, sidebar])`
5. `cv2.imshow(WINDOW_NAME, display)` shows the wider composite

The `add_status_line()` function was kept in the codebase (not deleted) in case it's needed for future overlays, but it is no longer called in the main display loop.

#### What stays on the camera frame

- Crosshair at frame centre
- Face detection bounding boxes and labels
- MTCNN landmark dots (eyes, nose, etc.)
- Face centre dot
- MLX90640 thermal inset (bottom-left with border and label)

These are all visual overlays that need to be seen in spatial context with the camera feed, so they belong on the frame rather than in a text sidebar.

#### What moved to the sidebar

- Tracking mode and servo angles
- Detector backend name
- Detection count
- Temperature correction values (raw, corrected, delta, assessment)
- Distance reading
- Ambient temperature, humidity, pressure, source, age
- Thermal summary statistics
- Detector error messages
- FPS counters
- Keyboard shortcut help

### Performance impact

The sidebar adds minimal overhead:
- One `np.full()` allocation per frame (300×480×3 = ~432KB — trivial)
- ~20 `cv2.putText()` calls per frame (microseconds each)
- One `np.hstack()` per frame (memory copy, ~1ms)
- Net FPS impact: negligible (<1 FPS difference)

### Verification

- `--test-once` mode ran successfully with no Python errors
- All sensors reported correctly (BME280, MLX90640, URM13, camera, servos)
- The `nvargus-daemon` needed a restart (known issue, not related to the sidebar change)

### Current overall status as of March 19, 2026

Working:

- Pi Camera V2
- MLX90640 thermal camera
- URM13 ultrasonic distance sensor
- BME280 ambient temperature/humidity/pressure sensor
- servo PWM control with relax system
- OpenCV DNN SSD face detection (threaded, ~3-5 FPS detection, ~25 FPS display)
- proportional auto-tracking with adaptive deadband
- forehead-targeted thermal region extraction
- sidebar UI with grouped sensor/status information
- Haar cascade fallback if DNN model files are missing
- FPS monitoring (display + detection shown separately)

Partially working:

- DHT22 on pin 31 / board.D6 (retained as fallback, still unreliable)

Not yet done:

- calibrated thermal/RGB alignment
- fitted temperature correction model (still heuristic, but now has reliable ambient input)
- live verification of forehead thermal targeting accuracy

## Servo Random Jump Fix — March 19, 2026

### Problem: servos occasionally lurch to random positions

During live testing, the user observed that the servos would sometimes make sudden, large, unexpected jumps — either to a completely wrong position or a dramatic overcorrection in the wrong direction. This happened intermittently and was distinct from the earlier oscillation and hunting problems. The user also recalled from earlier manual servo tuning that certain operations could trigger a sudden position jump.

### Root cause analysis

Two separate issues were identified:

#### Issue 1: PWM glitch on wake from relax state

When a servo woke from the relaxed state (PWM disabled), the `wake()` method was writing operations in this order:

```
1. write enable=1  (PWM output starts)
2. write duty_cycle  (correct pulse width)
```

The problem is that between step 1 and step 2, the PWM hardware is outputting whatever duty cycle value was left in its register from before the servo was disabled. On the Jetson Nano's sysfs PWM interface, disabling PWM (`enable=0`) does not guarantee that the duty_cycle register retains its exact previous value — the hardware may reset it to zero or an undefined state depending on the kernel driver's implementation.

When the PWM re-enables with a stale or zero duty cycle, the servo receives a brief pulse at the wrong width. A zero duty cycle means no pulse at all (servo goes limp momentarily), but a stale value from a different angle means the servo actively drives to the wrong position for the few milliseconds before the correct duty_cycle write arrives. SG90 servos respond to pulse changes within ~20ms (one PWM cycle at 50Hz), so even a brief wrong pulse is enough to cause a visible physical jump.

This explains the "certain values would make it jump randomly" observation from the servo tuner — any operation that involved disabling and re-enabling PWM (like switching between hold and relax modes) would trigger this glitch.

#### Issue 2: Detection outlier causing large tracking correction

The DNN SSD face detector running in the background thread occasionally produces:
- False positives at completely different positions in the frame
- Bounding boxes that jump significantly between frames (e.g. when the face is partially occluded and the detector snaps to a different feature)
- Brief misdetections where the bounding box shifts by 100+ pixels for a single detection cycle

When this happens, the face centre suddenly appears far from where it was last frame. The tracking system calculates a large error and commands a proportionally large correction (up to `TRACK_MAX_STEP = 4` degrees). If the detection was wrong, this is a 4-degree lurch in the wrong direction. By the next detection cycle (~200-400ms later), the face has moved out of frame entirely (because the servo moved the camera away from the subject), causing the tracker to lose the face completely or correct back equally aggressively.

This is different from the earlier oscillation problem (which was caused by stale detections + aggressive stepping). The oscillation was a sustained back-and-forth around the correct position. This new issue is a single large spike caused by a single bad detection.

### Fix 1: PWM wake order correction

The `wake()` method was rewritten to set the duty cycle **before** enabling PWM:

```python
def wake(self):
    # Set duty cycle to the correct position FIRST (while still disabled)
    if self.last_angle is not None:
        angle = max(0, min(180, int(self.last_angle)))
        pulse_us = MIN_PULSE_US + (angle / 180.0) * (MAX_PULSE_US - MIN_PULSE_US)
        duty_ns = int(pulse_us * 1000)
        self._write("duty_cycle", str(duty_ns))
    # THEN enable — servo starts at the correct position immediately
    self._write("enable", "1")
    self.relaxed = False
```

The duty cycle calculation is done inline in `wake()` rather than calling `set_angle()` to avoid the recursive call chain (`set_angle` → `wake` → `set_angle`). The duty_cycle write happens while PWM is still disabled, so the hardware register is updated before any pulses are generated. When `enable=1` is written, the first pulse out is already at the correct width.

This is a standard pattern for glitch-free PWM updates — always configure the waveform parameters before enabling the output. The previous implementation's `enable-then-configure` order is a known source of servo glitches in embedded PWM control.

### Fix 2: Detection jump filter

A new constant was added:

```python
DETECTION_JUMP_THRESHOLD = 150  # max pixels face centre can jump between frames
```

The tracking section now maintains `prev_face_center` — the face centre position from the previous tracking update. Before applying any tracking correction, the code checks whether the new face centre has jumped more than `DETECTION_JUMP_THRESHOLD` pixels from the previous position in either axis:

```python
jump_ok = True
if prev_face_center is not None:
    dx = abs(face_center[0] - prev_face_center[0])
    dy = abs(face_center[1] - prev_face_center[1])
    if dx > DETECTION_JUMP_THRESHOLD or dy > DETECTION_JUMP_THRESHOLD:
        jump_ok = False
prev_face_center = face_center  # always update for next comparison
```

If the jump exceeds the threshold, the tracking correction is skipped for that frame. Critically, `prev_face_center` is still updated to the new position — this means:

- A single-frame spike (false positive) is filtered: the bad detection is ignored, and the next good detection will be accepted because it'll be close to the previous good position
- Genuine fast movement is accepted within 2 frames: the first frame at the new position is filtered (looks like a jump), but the second frame at the new position matches the updated `prev_face_center` and passes

The 150px threshold was chosen because:
- The camera frame is 640x480 (or 480x640 after rotation)
- A face moving at normal speed across the frame covers ~30-50px between detection cycles (~200-400ms apart)
- Even fast head turns rarely exceed 80-100px between cycles
- A 150px jump almost certainly indicates a detection error or a completely different target
- The threshold is large enough that genuine movement is never filtered (even rapid head turns)

### Why these two issues often occurred together

The relax/wake cycle and the detection outlier problem are related through timing. The sequence was often:

1. Subject is stationary → servos relax (PWM disabled)
2. Detection thread produces a false positive at a distant position
3. Tracker sees large error → commands correction → calls `set_angle()` → `set_angle()` auto-wakes the servo
4. The wake glitch causes the servo to jump to a random position briefly
5. The servo then moves to the commanded (wrong) angle
6. The subject is now out of frame → tracker loses face or detects it at a new position → aggressive correction back

Steps 3-6 created the appearance of a single large random lurch, but it was actually two bugs compounding: the detection outlier decided to move, and the wake glitch made the physical movement even more erratic.

### Current tracking parameters

```python
TRACK_UPDATE_S = 0.10
TRACK_DEADBAND_MIN_X = 35
TRACK_DEADBAND_MIN_Y = 25
TRACK_DEADBAND_FACE_RATIO = 0.4
TRACK_MAX_STEP = 4
TRACK_GAIN = 0.5
SERVO_RELAX_DELAY = 0.5
DETECTION_JUMP_THRESHOLD = 150
```

### Sudo helper

A sudo command helper script was created on the Mac at `~/Desktop/projects/dis/sudo_helper.sh`. It creates a named pipe at `/tmp/sudo_pipe` and executes commands sent to it with root privileges. This allows the SSH-based workflow to run sudo commands on the Mac side without interactive password prompts. The user needs to start it once with `sudo bash ~/Desktop/projects/dis/sudo_helper.sh`.

### Nano internet access

The Nano was given internet access through the Mac for package installation:

- Nano default gateway set to `192.168.1.20` (Mac's en8 address)
- Nano DNS set to `8.8.8.8` via `/etc/resolv.conf`
- Mac IP forwarding was already enabled (`net.inet.ip.forwarding=1`)
- Mac NAT/PF was already configured
- Confirmed working: ping to `8.8.8.8` and `google.com` both succeed from the Nano

## GitHub Versioning and Thermal-RGB Alignment Work — April 24, 2026

The local dissertation folder was not previously a Git repository. Before
continuing the thermal/RGB alignment implementation, the folder was initialised
as a Git repository and connected to:

- `https://github.com/purkatron2000/Dissertation.git`

Initial baseline pushed to `main`:

- commit `e64c089`
- message: `Initial Jetson prototype baseline`
- included files:
  - `.gitignore`
  - `Depth-alignment-for-thermal-cameras.txt`
  - `jetson-nano-work-log.md`
  - `thermal_depth_alignment_app.py`

The baseline was intentionally created from the pre-alignment version of
`thermal_depth_alignment_app.py` so the thermal/RGB alignment work can be
reviewed separately.

Feature branch created:

- `codex/thermal-rgb-alignment`

Thermal/RGB alignment implementation started on the feature branch:

- added persistent alignment settings stored at:
  - `~/Desktop/thermal_rgb_alignment.json`
- added alignment parameters:
  - `scale_x`
  - `scale_y`
  - `offset_x`
  - `offset_y`
- changed `map_rgb_point_to_thermal()` so RGB-to-thermal mapping uses the
  loaded alignment settings instead of only fixed constants
- threaded the active alignment through forehead/face thermal ROI extraction
- added a calibration sidebar section showing current scale and offset values
- added live calibration mode toggled with `c`
- added keyboard controls in calibration mode:
  - `i` / `k` nudge thermal mapping up/down
  - `j` / `l` nudge thermal mapping left/right
  - `x` / `X` decrease/increase horizontal scale
  - `y` / `Y` decrease/increase vertical scale
  - `g` snap the mapping offset toward the hottest nearby thermal pixel
  - `s` save the current alignment JSON
  - `r` reset alignment to defaults
- added a thermal inset marker for the local hottest thermal point while in
  calibration mode

Local verification completed:

- `python3 -m py_compile thermal_depth_alignment_app.py` passes

Jetson deployment status:

- deployment and live calibration testing were not completed at this point
- the direct Ethernet link to the Nano was not up:
  - `en8` existed on the Mac
  - `en8` reported `media: autoselect (none)`
  - `ping -c 2 192.168.1.10` returned 100% packet loss
- the user may need to reconnect the Ethernet adapter/cable and rerun the
  Mac-side static networking commands before SSH/SCP deployment can continue

### Jetson deployment follow-up

The user restored the direct Ethernet link and confirmed ping to the Nano.
Password-based SSH was then used because the Mac SSH key passphrase was not
available to the automation process.

Deployment completed:

- backed up the previous Nano desktop app to:
  - `~/Desktop/thermal_depth_alignment_versions/thermal_depth_alignment_app_pre_alignment_20260319_165442.py`
- copied the feature-branch `thermal_depth_alignment_app.py` to:
  - `~/Desktop/thermal_depth_alignment_app.py`
- remote syntax check passed:
  - `python3 -m py_compile thermal_depth_alignment_app.py`

`--test-once` result with the deployed alignment app:

- `alignment`: default values loaded
  - `scale_x=1.0`
  - `scale_y=1.0`
  - `offset_x=0.0`
  - `offset_y=0.0`
- `alignment_status`: `no saved alignment`
- `camera`: `True`
- `detector`: `dnn_ssd`
- `detector_errors`: `ok`
- `detections`: `0`
- `distance_cm`: `296`
- `ambient`: BME280 working
  - approximately `20.12 C`
  - approximately `33.48% RH`
  - approximately `1017.21 hPa`
- `servo`:
  - `pan_ready=True`
  - `tilt_ready=True`
- `thermal`: failed
  - `thermal_error: No I2C device at address: 0x33`

I2C verification:

- `i2cdetect -y 1` did not show `0x33`
- direct read from URM13 address `0x12` succeeded
- BME280 chip ID read from `0x76` returned `0x60`
- direct read from MLX90640 address `0x33` failed

Regression check:

- the backed-up pre-alignment app was run with `--test-once`
- it produced the same MLX90640 failure:
  - `thermal_error: No I2C device at address: 0x33`
- this confirms the current thermal failure is not caused by the new alignment
  code

Current blocker:

- live thermal/RGB calibration cannot be completed until the MLX90640 is visible
  again on I2C bus 1 at address `0x33`
- likely next physical checks:
  - verify MLX90640 power and ground
  - verify SDA/SCL wiring to the shared I2C bus
  - check whether the thermal module has become unplugged or loose
  - power-cycle the rig if wiring appears correct

### Separate no-servo alignment calibrator

The user requested that alignment testing be separated from the main app so
servo movement cannot nudge the fragile thermal-camera connection.

New standalone file created locally and deployed to the Nano:

- local:
  - `thermal_rgb_alignment_calibrator.py`
- Nano:
  - `~/Desktop/thermal_rgb_alignment_calibrator.py`

Purpose:

- test and tune RGB-to-thermal alignment only
- keep the RGB camera, DNN detector, MLX90640 thermal feed, URM13 distance,
  BME280 ambient readings, sidebar, and thermal ROI display
- avoid any pan/tilt servo interaction during alignment tests

Servo safety changes in the calibrator:

- no `HardwarePWMServo(...)` instances are created
- no PWM export or servo centering occurs
- auto-tracking is disabled
- arrow keys and space bar do not move servos
- `--test-once` reports:
  - `servo: disabled in alignment calibrator`

Calibration controls in the separate app:

- starts in calibration mode
- `i` / `k`: nudge thermal mapping up/down
- `j` / `l`: nudge thermal mapping left/right
- `x` / `X`: decrease/increase horizontal scale
- `y` / `Y`: decrease/increase vertical scale
- `g`: snap mapping offset toward the hottest thermal point
- `s`: save current alignment to `~/Desktop/thermal_rgb_alignment.json`
- `r`: reset alignment to defaults
- `c`: toggle calibration mode
- `q`: quit

Verification completed:

- local syntax check passed:
  - `python3 -m py_compile thermal_rgb_alignment_calibrator.py`
- remote syntax check passed:
  - `python3 -m py_compile thermal_rgb_alignment_calibrator.py`
- remote `--test-once` passed with:
  - `camera: True`
  - `detector: dnn_ssd`
  - `detector_errors: ok`
  - `distance_cm: 248`
  - BME280 ambient reading working
  - MLX90640 thermal frame working
  - `servo: disabled in alignment calibrator`

The no-servo calibrator was launched on the Jetson desktop with:

```bash
cd ~/Desktop && DISPLAY=:0 python3 thermal_rgb_alignment_calibrator.py
```

The app is now the active alignment test program. The main integrated app is
not being run for this test.

### Distance-aware alignment correction

During live use, the user observed that a single fixed thermal/RGB offset is
not useful because alignment changes with subject distance. This is expected:
the RGB and thermal cameras are physically separated, so parallax means the
correct thermal offset varies with depth.

Important design correction:

- the first implemented calibration idea was a simple fixed offset/scale model
- it let the operator nudge the mapped thermal point until it lined up at one
  specific standing position
- this was useful as a quick diagnostic because it proved the overlay could be
  adjusted live and saved
- however, it did not solve the actual project problem

Why the basic offset approach failed:

- aligning at one distance only makes the two camera views agree at that
  particular depth
- when the user moved closer or farther away, the mapped thermal point drifted
  away from the real warm face/blob
- this is not just a tuning mistake; it is caused by parallax between the RGB
  camera and the MLX90640 thermal camera
- because the cameras are not in exactly the same physical location, the
  apparent relative position of the subject changes with distance
- therefore one global `(offset_x, offset_y)` cannot be correct for all subject
  distances

Rejected approach:

- continuing to tune one fixed offset would only produce a calibration that
  works for the exact position where it was tuned
- that would not be useful for the intended system because a user will not
  stand at one fixed distance from the rig
- it would also make later temperature extraction misleading, because the
  thermal ROI could appear correct during calibration but be wrong during real
  use at another depth

Revised design reasoning:

- the system already has a URM13 distance sensor
- instead of treating distance only as an input to temperature correction, it
  can also be used to select the correct RGB-to-thermal alignment
- the practical calibration target is therefore not one offset, but a small
  table of offsets measured at different distances
- for a distance between two measured points, linear interpolation is a
  reasonable first approximation
- for distances outside the measured range, the nearest calibration sample is
  used until more data is collected

The separate no-servo calibrator was updated again to use distance-aware
alignment samples rather than one fixed offset.

New calibration model:

- `thermal_rgb_alignment_calibrator.py` now stores:
  - global `scale_x`
  - global `scale_y`
  - default `offset_x`
  - default `offset_y`
  - `distance_samples`
- each distance sample contains:
  - `distance_cm`
  - `offset_x`
  - `offset_y`
- at runtime, the app uses the live URM13 `distance_cm` reading to interpolate
  between the two nearest stored samples
- if the subject is closer than the nearest sample, the nearest close sample is
  used
- if the subject is farther than the farthest sample, the nearest far sample is
  used

Updated workflow:

1. Put the subject/warm target at one distance.
2. Use `i` / `k` / `j` / `l` to nudge the mapped thermal point onto the real
   hot region.
3. Press `a` to add or update a calibration sample at the current URM13
   distance.
4. Move the subject/warm target to another distance.
5. Repeat the nudge and `a` sample process.
6. Press `s` to save all samples to:
   - `~/Desktop/thermal_rgb_alignment.json`

Updated controls:

- `i` / `k`: temporary nudge up/down for the current distance
- `j` / `l`: temporary nudge left/right for the current distance
- `a`: add/update the current distance sample using the current nudge-adjusted
  offset
- `s`: save the full distance sample table
- `g`: snap the current temporary nudge toward the hottest thermal point
- `r`: reset the full alignment model
- `x` / `X`: adjust global horizontal scale
- `y` / `Y`: adjust global vertical scale
- `q`: quit

Verification:

- local syntax check passed
- remote syntax check passed
- remote `--test-once` passed with:
  - camera working
  - DNN SSD detector working
  - BME280 working
  - URM13 distance working
  - MLX90640 thermal frame working
  - servo disabled
  - zero initial distance samples

The upgraded distance-aware no-servo calibrator was relaunched on the Jetson
desktop for testing.

### Saved distance-aware alignment sample set

The user completed a manual calibration pass and pressed `s` to save the
distance-aware alignment model.

Important status note:

- this records that a distance-aware alignment model was saved
- it does **not** mean the model has been validated as correct yet
- the next step is to move the subject to several distances and check whether
  the mapped thermal marker stays aligned with the real hot region without
  further manual nudging

Saved file on the Nano:

- `~/Desktop/thermal_rgb_alignment.json`

The saved file was copied back locally as:

- `thermal_rgb_alignment_saved.json`

Saved alignment contents:

```json
{
  "distance_samples": [
    {
      "distance_cm": 53.0,
      "offset_x": -3.0,
      "offset_y": 3.0
    },
    {
      "distance_cm": 78.0,
      "offset_x": -8.369402985074627,
      "offset_y": 2.0242537313432836
    },
    {
      "distance_cm": 93.0,
      "offset_x": -6.791044776119403,
      "offset_y": -1.7611940298507465
    },
    {
      "distance_cm": 122.0,
      "offset_x": -2.259701492537314,
      "offset_y": 0.8746268656716416
    }
  ],
  "offset_x": 0.0,
  "offset_y": 0.0,
  "scale_x": 1.0,
  "scale_y": 1.0,
  "updated_at": "2026-03-19 17:24:11"
}
```

Interpretation:

- although the user took approximately five or six measurements, the saved
  model contains four samples
- this is expected because the calibrator merges samples that are within
  `ALIGNMENT_SAMPLE_MERGE_CM = 8` cm of an existing sample
- the current saved calibration range is approximately:
  - close: `53 cm`
  - middle: `78 cm`
  - middle/far: `93 cm`
  - far: `122 cm`
- for subject distances between those points, the app interpolates offset
  values linearly
- for subject distances closer than `53 cm` or farther than `122 cm`, the
  nearest saved sample is used

Notable result:

- the horizontal offset is not constant across distance:
  - `-3.0` px at `53 cm`
  - about `-8.37` px at `78 cm`
  - about `-6.79` px at `93 cm`
  - about `-2.26` px at `122 cm`
- this confirms the earlier observation that one fixed offset is not adequate
  for this camera rig

Validation status:

- pending live visual test across multiple distances
- success criteria should be:
  - at close, middle, and far distances, the mapped thermal marker remains on
    the warm face/blob without needing a new manual nudge
  - the sidebar sample count remains stable
  - the displayed effective offset changes as the URM13 distance changes

### Distance-only model rejected and guided regression calibrator

The user tested the distance-aware offset model and reported that it still
looked effectively random. The key observation was that distance alone cannot
account for:

- where the face appears in the RGB image
- whether the face is near the image boundary
- the viewing angle between the RGB and thermal cameras
- the lateral/vertical parallax caused by the two sensors being mounted in
  different physical positions

This invalidated the distance-only offset table as the final calibration
approach. It was useful as a diagnostic step, but still too low-dimensional for
the actual alignment problem.

Revised calibration target:

Instead of modelling only:

```text
thermal_offset = f(distance)
```

the calibrator now builds samples for:

```text
thermal_x, thermal_y = f(rgb_x, rgb_y, distance)
```

This directly models where the face appears in the RGB frame and how far away
it is.

Guided data collection design:

- the no-servo calibrator now presents target positions on the RGB image
- the user moves their face/head to the requested target position
- the sidebar tells the user which distance to aim for
- the user presses `a` to capture a sample
- each sample records:
  - RGB face centre `rgb_x`, `rgb_y`
  - RGB frame size
  - detected thermal hotspot `thermal_x`, `thermal_y`
  - thermal frame size
  - URM13 `distance_cm`
  - requested target position and target distance
  - timestamp

Planned sample grid:

- distances:
  - `50 cm`
  - `80 cm`
  - `110 cm`
  - `140 cm`
- image positions at each distance:
  - top-left
  - top-centre
  - top-right
  - middle-left
  - centre
  - middle-right
  - bottom-left
  - bottom-centre
  - bottom-right
- total planned captures:
  - `4 distances x 9 positions = 36 samples`

Fitting model:

- after at least `9` samples, the calibrator fits two least-squares regression
  models:
  - one for `thermal_x`
  - one for `thermal_y`
- features used:
  - constant term
  - normalised `rgb_x`
  - normalised `rgb_y`
  - inverse distance `1 / distance_cm`
  - `rgb_x * (1 / distance_cm)`
  - `rgb_y * (1 / distance_cm)`
  - `rgb_x * rgb_y`
  - `rgb_x^2`
  - `rgb_y^2`
- this is still simple enough to explain in the report, but it is much more
  appropriate than a fixed offset or a distance-only offset

Live model feedback:

- once fitted, the program displays the predicted thermal point
- it also compares that prediction with the current thermal hotspot and reports
  live error in thermal pixels
- saved model file:
  - `~/Desktop/thermal_rgb_guided_alignment.json`

Updated guided controls:

- `a`: capture current sample and advance to the next target
- `f`: fit/re-fit the regression model from current samples
- `s`: save the guided model JSON
- `n`: skip current target
- `b`: go back one target
- `r`: reset guided samples/model
- `q`: quit

Verification before live use:

- local syntax check passed
- remote syntax check passed
- remote `--test-once` passed with:
  - `camera: True`
  - `detector: dnn_ssd`
  - `distance_cm: 52`
  - BME280 working
  - MLX90640 thermal frame working
  - `servo: disabled in alignment calibrator`
  - no saved guided model yet

The guided no-servo regression calibrator was deployed to:

- `~/Desktop/thermal_rgb_alignment_calibrator.py`

and launched on the Jetson desktop.

### 140 cm guided calibration stage removed

During the guided regression calibration, the user reported that alignment
looked accurate until the `140 cm` captures were added, after which the fitted
model became poor again. This indicates that the far-distance samples were
likely low quality or outside the range where the current setup gives reliable
thermal/RGB correspondences.

Action taken:

- stopped the running guided calibrator
- removed `140 cm` from `GUIDED_DISTANCES_CM`
- current guided distances are now:
  - `50 cm`
  - `80 cm`
  - `110 cm`
- total planned captures changed from:
  - `4 distances x 9 positions = 36 samples`
  - to `3 distances x 9 positions = 27 samples`

Additional safety tools added to the calibrator:

- `u`: undo the last captured sample
- `w`: disable the worst residual sample after fitting
- `v`: re-enable all samples
- the sidebar now reports enabled/total sample count
- the sidebar now reports the worst sample and its error after fitting

Reasoning:

- a single bad sample can distort the least-squares regression, especially when
  the dataset is still small
- excluding the farthest distance for now should make it easier to fit a stable
  useful model over the main working range
- outlier controls make future failed captures recoverable without restarting
  the whole calibration session

Verification:

- local syntax check passed
- remote syntax check passed after deployment
- the updated calibrator was relaunched on the Jetson desktop

### Thermal corner hotspot fault during guided alignment

Time: `2026-04-24 15:11:32 BST`

While preparing to redo the guided calibration without the `140 cm` stage, the
user reported that the MLX90640 thermal image consistently marked the top-right
corner as the hottest point, even when the physical camera was moved and there
was no hot object in that part of the room. This made the visible thermal cross
stick to the corner and made calibration captures unreliable.

Interpretation:

- this looks like a bad/stuck thermal pixel or read artefact rather than a real
  heat source
- whole-frame hottest-pixel tracking is therefore not suitable for calibration
- this does not invalidate the RGB face detection or guided target method, but
  it means the thermal correspondence must be found near the expected face area
  instead of using the global maximum

Action taken in the no-servo calibrator:

- ignored the outer thermal image border when searching for hot points
- stopped using the whole-frame thermal maximum for guided captures
- added a local thermal search around the expected face position:
  - before a model is fitted, this uses the rough normalized RGB-to-thermal
    position
  - after a model is fitted, this uses the model prediction
- kept servo movement disabled

Reasoning:

- a fixed corner artefact can dominate the full-frame maximum even when the
  user's face is correctly visible in the thermal frame
- restricting the thermal search to the area where the face should project makes
  the captured correspondence much more likely to represent the user's head
- this is still a calibration aid, not the final tracking model; once enough
  clean samples are captured, the regression model should predict thermal/RGB
  alignment from face position and distance

Current status:

- updated calibrator is running on the Jetson desktop
- next step is to verify visually that the thermal cross no longer sticks to the
  top-right corner during guided capture

### Correction to initial guided thermal search

Time: `2026-04-24 15:16:00 BST`

The user pointed out that the guided calibrator was still limiting the thermal
search to a small area around an uncalibrated rough projection during the first
round of captures. That was the wrong dependency: the first captures are meant
to discover the projection, so they cannot be forced to stay near an estimate
that may already be far off.

Action taken:

- changed the pre-fit calibration behaviour so it searches the full usable
  thermal frame, excluding only the ignored border
- added 3x3 thermal smoothing before selecting the warm point, so isolated
  stuck/bad pixels have less influence
- kept the local search behaviour only for after a guided model has been fitted
- redeployed the no-servo calibrator to the Jetson and restarted it on
  `DISPLAY=:0`

Reasoning:

- before a model exists, the user needs freedom to gather calibration samples
  wherever the thermal face blob actually appears
- after a model exists, a local search around the prediction is useful because
  it prevents unrelated warm regions from stealing the measurement
- this separates data collection from model validation instead of allowing an
  untrained estimate to block calibration

Verification:

- local syntax check passed
- remote syntax check passed
- updated calibrator launched on the Jetson desktop

### Robust thermal blob selection implemented

Time: `2026-04-24 15:22:21 BST`

The user reported that the thermal cross still behaved inconsistently: the
display visibly showed hotter face/forehead regions, but the selected cross
could appear over a colder-looking purple area. This meant the selection path
was still not trustworthy enough for calibration.

Problems identified:

- a previously saved bad guided model could be loaded on startup and immediately
  pull the local thermal search back toward a wrong prediction
- the calibration capture path should not depend on the current model at all
  while collecting data
- a single max pixel or a small artefact is not a robust representation of the
  user's face heat blob

Action taken:

- changed guided calibration so the capture hotspot always comes from a robust
  full-frame thermal blob search, independent of the loaded/fitted model
- kept the fitted model prediction only as a comparison point for live error
  display, not as the source of calibration captures
- replaced direct hottest-pixel selection with:
  - finite-value cleanup
  - 3x3 spatial smoothing
  - border ignore
  - high-percentile hot mask
  - connected-component selection of the strongest warm blob
  - rejection of tiny isolated blobs during full-frame search
- hardened the thermal inset display against non-finite thermal values
- started the Jetson calibrator clean so old bad guided data is not mixed into
  the next calibration fit

Reasoning:

- calibration data collection must measure what the thermal camera currently
  sees, not what a partly wrong model expects to see
- using a connected warm blob better matches the visible thermal face region and
  is less sensitive to stuck pixels, I2C artefacts, and single-pixel spikes
- old saved bad models/samples should not silently influence the next
  calibration attempt

Verification:

- local syntax check passed
- updated file was deployed to the Jetson
- remote syntax check passed
- no-servo calibrator relaunched on the Jetson desktop

### Guided alignment model saved

Time: `2026-04-24 15:37:28 BST`

The user completed the guided calibration run, pressed `f` to fit the model,
then pressed `s` to save it. The saved model was copied back from the Jetson for
inspection.

Saved model file:

- Jetson: `~/Desktop/thermal_rgb_guided_alignment.json`
- Local copy: `thermal_rgb_guided_alignment.json`

Model summary:

- saved timestamp inside JSON: `2026-03-19 18:40:04` from the Jetson clock
- total samples: `27`
- enabled samples: `27`
- sample distribution:
  - `50 cm`: `9`
  - `80 cm`: `9`
  - `110 cm`: `9`
- fit RMSE: `0.3918` thermal pixels
- mean error: `0.3400` thermal pixels
- max error: `0.8875` thermal pixels
- worst sample:
  - index: `10`
  - target: `80 cm`, `x=0.50`, `y=0.25`
  - measured distance: `77 cm`
  - error: `0.8875` thermal pixels

Interpretation:

- the saved dataset is complete for the current `3 distances x 9 positions`
  calibration plan
- the residual errors are low in thermal-pixel space, which suggests the model
  fits the captured calibration points well
- this still needs live validation by moving around the image and checking that
  the model prediction remains aligned with the measured thermal face/forehead
  blob

### Branching and main-app integration of guided model

Time: `2026-04-24 15:42:26 BST`

The standalone no-servo calibration work was preserved separately before
changing the main application.

Git branch created for the calibration test program:

- branch: `codex/thermal-rgb-calibrator-test`
- commit: `651957c Add standalone thermal RGB calibration test`
- pushed to GitHub remote: `origin/codex/thermal-rgb-calibrator-test`

Scope of the calibrator branch:

- `thermal_rgb_alignment_calibrator.py`
- `thermal_rgb_guided_alignment.json`
- `thermal_rgb_alignment_saved.json`
- `jetson-nano-work-log.md`

Reason for keeping this branch separate:

- the calibrator is an experimental/training tool, not the main deployed app
- it disables servo movement so the camera rig is not nudged during calibration
- it guided the user through multiple positions and distances to collect a
  dataset for RGB-to-thermal alignment
- it contains the development history of the alignment process, including the
  approaches that failed or were replaced

Development history recorded for the alignment method:

- first approach: fixed offset/scale alignment
  - result: rejected because alignment changed with distance
- second approach: distance-aware offset samples
  - result: still not enough because face position in the RGB image also affects
    the thermal/RGB mapping
- third approach: guided 2D position plus distance calibration
  - user moved to a 3x3 grid at each distance and pressed `a` to capture samples
  - model fitted thermal `x/y` from RGB face centre and measured distance
- `140 cm` issue:
  - initial guided plan included `50`, `80`, `110`, and `140 cm`
  - user reported that the fit looked good until the `140 cm` samples were
    included
  - `140 cm` was removed from the active calibration plan
  - current model uses `50`, `80`, and `110 cm`
- thermal hot-corner issue:
  - MLX90640 sometimes reported the top-right corner as hottest even when it was
    not physically hot
  - calibrator was changed to ignore the 2-pixel border and use robust warm-blob
    selection instead of a single hottest pixel
- bad design corrected:
  - early calibrator versions limited thermal search around an untrained
    estimate
  - this was removed because calibration must not depend on the model before the
    model exists
  - capture now measures the robust thermal blob directly, then the model is
    fitted afterwards

Main-app integration plan:

- create a separate integration branch for the main application
- add guided-model loading to `thermal_depth_alignment_app.py`
- read the saved model from `~/Desktop/thermal_rgb_guided_alignment.json`
- use the guided regression prediction for the thermal face/forehead point when
  coefficients are available
- keep the older offset/scale alignment as a fallback if no guided model exists
- keep the old manual calibration controls available, but make normal operation
  prefer the trained guided model

Implementation completed in the main app:

- added `GUIDED_ALIGNMENT_FILE`
- added guided model load/readiness helpers
- added the same RGB-position-plus-distance feature vector used by the
  standalone calibrator
- added guided thermal point prediction from saved `coeff_x`/`coeff_y`
- changed main thermal point selection to prefer the guided model and fall back
  to the old offset/scale mapper only if the model is missing
- when guided mode is active, the thermal ROI is centred on the predicted
  thermal forehead/face point rather than remapping landmarks with the old
  offset model
- added robust thermal blob selection and finite-value handling to the main app
  so calibration/debug hotspot overlays are less vulnerable to stuck pixels
- updated the sidebar to report `Mode: guided RGB+distance` and show the saved
  fit RMSE/sample count when the guided model is active

Verification so far:

- local Python syntax check passed for:
  - `thermal_depth_alignment_app.py`
  - `thermal_rgb_alignment_calibrator.py`

Deployment update:

- time: `2026-04-24 15:43:36 BST`
- copied updated `thermal_depth_alignment_app.py` to the Jetson Desktop
- copied the saved guided model JSON to the Jetson Desktop
- stopped the standalone no-servo calibrator so the RGB camera was free
- remote syntax check passed on the Jetson
- launched the main app on the Jetson display with:
  - `DISPLAY=:0 python3 thermal_depth_alignment_app.py`
- running process observed:
  - `python3 thermal_depth_alignment_app.py`

Expected live behaviour in the main app:

- sidebar should report `Mode: guided RGB+distance` if the saved model loaded
- white point/ROI in the thermal inset should come from the trained guided
  model, not from the old fixed offset model
- if the guided model is absent or invalid, this branch should error rather than
  falling back to old offset/scale alignment
- standalone calibration program remains separate and is not required for normal
  main-app operation after the model has been saved

### Main app changed to require guided model only

Time: `2026-04-24 15:45:04 BST`

The user clarified that the main program must not fall back to the old alignment
options. This is important because a fallback would make testing ambiguous: the
program could appear to work while actually using the old fixed offset/scale
path instead of the trained guided model.

Action taken:

- removed runtime fallback from guided prediction to old offset/scale mapping
- main UI now raises a `RuntimeError` on startup if
  `~/Desktop/thermal_rgb_guided_alignment.json` is missing or does not contain
  valid `coeff_x` and `coeff_y`
- if guided prediction somehow returns `None` during runtime, the app now errors
  instead of silently mapping through the old alignment path
- sidebar no longer advertises `c:calibrate` in normal mode
- pressing `c` no longer enters the old manual calibration mode; it reports that
  guided-model alignment is required and old calibration is disabled
- sidebar wording for missing model was changed from offset fallback to guided
  model missing

Reasoning:

- this branch is specifically for validating the new learned alignment model
- silently using the old model would invalidate the test
- failure should be obvious and immediate if the guided model is not available

Verification:

- local syntax check passed for `thermal_depth_alignment_app.py`

### Post-Easter thermal camera connection reliability note

Time: `2026-04-24 15:49:02 BST`

The user noted that, after returning from the Easter break, the MLX90640 thermal
camera connection appears noticeably less reliable than it was before the break.
This is an important hardware/context observation rather than a confirmed
software regression.

Observed behaviour during the alignment work:

- intermittent thermal camera failures and read issues occurred while the RGB
  camera, distance sensor, and other parts of the app were otherwise working
- the thermal camera sometimes needed the physical connection or rig position
  adjusted before it behaved normally again
- earlier in the session the user reported messages/behaviour consistent with
  bad thermal frames, including the sensor repeatedly favouring an apparently
  false hot region near the top-right of the thermal image
- the thermal feed could recover after movement/reseating, suggesting a possible
  physical connection, cable, soldering, I2C contact, or sensor-board stability
  issue

Impact on development:

- the standalone no-servo calibration program was kept separate partly to avoid
  servo movement nudging the camera or worsening the intermittent connection
- robust thermal blob selection and border rejection were added to reduce the
  effect of bad/stuck thermal readings during calibration
- future testing should treat MLX90640 connection stability as a hardware risk
  and verify the thermal feed before collecting calibration data
