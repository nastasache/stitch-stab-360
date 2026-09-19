# Camera Profiles & Optical Calibration Files (`others/`)

This directory contains specialized camera lens profiles, Gyroflow stabilization definitions, and OpenCV optical calibration matrices for the Samsung Gear 360 (SM-C200) and equirectangular 360° video pipelines.

Unlike the pipeline configuration presets in [`presets/`](../presets/) (which store UI rendering options, FOV offsets, and stabilization toggles), the files in this directory define **mathematical optical models**, **lens distortion parameters**, and **stereo alignment matrices**.

---

## Directory Overview

```
others/
├── README.md                                  # This documentation file
│
├── Gyroflow Lens Profiles (JSON)
│   ├── Equirectangular_360_3840x1920.json      # Gyroflow profile for 3840x1920 equirectangular 360° video
│   ├── Gear360_Equirectangular_3840x1920.json  # Gyroflow profile for 3840x1920 stitched 360° video
│   ├── Gear360_Stitched_3840x1920.json         # Gyroflow preset for stitched equirectangular footage
│   ├── Gyroflow_Gear360_Stitched_3840x1920.json# Gyroflow alias profile
│   ├── Samsung_Gear360_SM-C200_3840x1920.json  # Calibrated profile for raw dual fisheye (3840x1920)
│   ├── Samsung_Gear360_SM-C200_1920x1920.json  # Calibrated profile for single fisheye lens (1920x1920)
│   ├── 360_Equirectangular_Panorama.json       # Standard 360° panorama lens profile
│   └── equirectangular.json                    # Minimal equirectangular camera projection definition
│
└── Optical & Stereo Calibration Matrices (JSON)
    ├── camera_hardware_calibration.json        # Global joint multi-frame camera calibration data
    └── dual_fisheye_calibration.json           # Dual-fisheye optical stitching & stereo transform model
```

---

## 1. Gyroflow Lens Profiles

These files adhere to the [Gyroflow Lens Profile Specification](https://gyroflow.xyz) and are used for gyroscope-based stabilization and horizon leveling.

### 📷 Stitched 360° Equirectangular Profiles
Used when stabilizing an already-stitched 360° video with embedded or external gyro telemetry.

* **`Equirectangular_360_3840x1920.json`**
  * **Input Video:** 3840×1920 Equirectangular 360°
  * **Lens Model:** OpenCV Fisheye Model (fx = fy = 1222.392, Center: 1920.0, 960.0)
  * **Use Case:** Stabilizing and leveling 3840×1920 equirectangular 360° stitched videos in Gyroflow.

* **`Gear360_Equirectangular_3840x1920.json`**
  * **Input Video:** 3840×1920 Stitched Equirectangular
  * **Lens Model:** Equirectangular 360° (OpenCV fisheye model with zero radial distortion limit)
  * **Focal Length:** fx = fy = 611.15, Center: (1920.0, 960.0)
  * **Use Case:** Loading into Gyroflow to level and stabilize stitched Gear 360 footage.

* **`Gear360_Stitched_3840x1920.json` & `Gyroflow_Gear360_Stitched_3840x1920.json`**
  * **Input Video:** 3840×1920 Stitched Equirectangular
  * **Use Case:** Profile variants configured for Gyroflow preset manager and automated profile matching.

* **`360_Equirectangular_Panorama.json`**
  * **Input Video:** 3840×1920 Equirectangular
  * **Lens Model:** OpenCV Standard Projection Model (fx = fy = 1222.3, center: 1920, 960)
  * **Use Case:** Alternative standard projection mapping for 360° panoramic stabilization.

* **`equirectangular.json`**
  * **Lens Type:** Equirectangular 360.0° FOV
  * **Use Case:** Lightweight camera projection definition used for coordinate transforms.

---

### 📷 Raw Fisheye Lens Profiles
Used when processing unstitched dual-lens or single-lens raw footage directly from the camera sensor.

* **`Samsung_Gear360_SM-C200_3840x1920.json`**
  * **Input Video:** 3840×1920 side-by-side dual fisheye stream
  * **FOV:** 190.8° fisheye
  * **Focal Length:** fx = fy = 576.56, Center: (960.0, 960.0)
  * **Use Case:** Stabilizing raw side-by-side circular fisheye frames before stitching.

* **`Samsung_Gear360_SM-C200_1920x1920.json`**
  * **Input Video:** 1920×1920 single circular fisheye crop
  * **FOV:** 190.8° fisheye
  * **Use Case:** Stabilizing isolated single-lens streams.

---

## 2. Optical & Stereo Calibration Matrices

These files store intrinsic and extrinsic calibration parameters used by the Python stitching engine (`utils/stitch_opencv.py`) and calibration tools (`utils/calibrate_dual_fisheye.py`, `utils/global_calibrate.py`).

### 📐 `dual_fisheye_calibration.json`
Comprehensive optical calibration configuration for dual-lens stitching:
* **Front & Rear Lens Intrinsic Parameters:**
  * Optical centers (cx, cy), radius, focal lengths (fx, fy).
  * Field of View (195.0° diagonal).
  * Polynomial distortion coefficients (k1, k2, k3, k4).
* **Stereo Extrinsic Transform:**
  * Rotation vector (R) and translation vector (T) modeling the optical offset between front and rear sensors.
  * Baseline distance: ~18.5 mm.
* **Blending & Color Parameters:**
  * Multi-band spline blending levels, blend margin (15.0°), exposure gain compensation.

### 📐 `camera_hardware_calibration.json`
Master calibration record generated across multi-frame video datasets by `utils/global_calibrate.py`:
* Contains global roll/pitch/yaw offsets, left-lens vertical adjustments, and mean disparity pixel scores across calibration frame samples.

---

## How to Use These Files

1. **In Gyroflow:**
   * Open Gyroflow -> **Lens profile** -> **Open from file...** -> Select any `.json` lens profile from this `others/` folder.
2. **In OpenCV Stitching Pipeline:**
   * Run:
     ```bash
     python -B utils/stitch_opencv.py --input "work/input.mp4" --calibration "others/dual_fisheye_calibration.json" --output "done/output.mp4"
     ```
3. **To Recalibrate:**
   * Run `utils/calibrate_dual_fisheye.py` to export updated calibration parameters into `others/dual_fisheye_calibration.json`.
