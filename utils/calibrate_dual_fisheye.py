#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
"""Dual-fisheye 360° camera calibration parameter management and data export.

Implements Kannala-Brandt fisheye single-lens calibration, stereo lens-to-lens
transform recovery, equirectangular projection mapping, seam optimization, and
exposure compensation parameter serialization.
"""

import sys
import os
import json
import math
import argparse
from datetime import datetime

# Optional import for OpenCV & NumPy if available in environment
HAS_OPENCV = False
try:
    import cv2
    import numpy as np
    HAS_OPENCV = True
except ImportError:
    cv2 = None
    np = None


class DualFisheyeCalibration:
    """Class to manage, store, load, and export dual-fisheye 360° calibration parameters."""

    def __init__(self, name="Dual_Fisheye_Calibration", width=3840, height=1920):
        """Initialize dual-fisheye calibration structure with default parameters.

        Args:
            name: Human-readable identifier for the camera calibration.
            width: Output equirectangular panorama width in pixels.
            height: Output equirectangular panorama height in pixels.
        """
        self.metadata = {
            "name": name,
            "created_at": datetime.now().isoformat(),
            "lens_model": "fisheye_kannala_brandt",  # Maps pixels to viewing rays
            "calib_dimension": {"w": width, "h": height},
            "official": False,
            "calibrated_by": "Antigravity IDE Calibration Tool"
        }

        # Step 1: Lens intrinsics (Front and Rear Lenses)
        # K = [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]
        # D = [k1, k2, k3, k4] (Fisheye distortion coefficients)
        self.front_lens = {
            "camera_matrix": [
                [960.0, 0.0, 960.0],
                [0.0, 960.0, 960.0],
                [0.0, 0.0, 1.0]
            ],
            "distortion_coeffs": [0.0, 0.0, 0.0, 0.0],  # Corrects barrel distortion
            "optical_center": {"cx": 960.0, "cy": 960.0},  # Exact center of front fisheye circle
            "effective_focal_length": {"fx": 960.0, "fy": 960.0},  # Determines FOV
            "fov_degrees": 195.0,
            "rms_error": 0.0
        }

        self.rear_lens = {
            "camera_matrix": [
                [960.0, 0.0, 960.0],
                [0.0, 960.0, 960.0],
                [0.0, 0.0, 1.0]
            ],
            "distortion_coeffs": [0.0, 0.0, 0.0, 0.0],
            "optical_center": {"cx": 960.0, "cy": 960.0},  # Exact center of rear fisheye circle
            "effective_focal_length": {"fx": 960.0, "fy": 960.0},
            "fov_degrees": 195.0,
            "rms_error": 0.0
        }

        # Step 2: Stereo Transform (Front ----R,T---- Rear)
        self.stereo_transform = {
            "rotation_matrix_R": [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0]
            ],  # Aligns the two lenses
            "rotation_vector_rvec": [0.0, 0.0, 0.0],
            "translation_vector_T": [0.0, 0.0, 0.02],  # Lens spacing / baseline (meters)
            "stereo_rms_error": 0.0
        }

        # Step 3: Projection Map Parameters
        self.projection_map = {
            "panorama_width": width,
            "panorama_height": height,
            "front_fov_deg": 195.0,
            "rear_fov_deg": 195.0,
            "interpolation_method": "INTER_LINEAR",
            "map_type": "equirectangular"
        }

        # Step 4 & 5: Seam Mask & MultiBand Blending
        self.seam_and_blend = {
            "seam_mask_path": "data/input/masks/seam_mask.png",
            "seam_finder": "GraphCutSeamFinder",  # Options: GraphCutSeamFinder, DpSeamFinder
            "blender_type": "MultiBandBlender",  # Options: MultiBandBlender, AlphaBlender
            "num_bands": 5,
            "blend_width_pixels": 64,
            "overlap_angle_deg": 15.0
        }

        # Step 6: Exposure & White Balance Correction
        self.color_compensation = {
            "exposure_compensator": "ExposureCompensator_GAIN_BLOCKS",
            "front_exposure_gain": 1.0,
            "rear_exposure_gain": 1.0,
            "front_exposure_offset": 0.0,
            "rear_exposure_offset": 0.0,
            "white_balance_correction": {
                "r_gain": 1.0,
                "g_gain": 1.0,
                "b_gain": 1.0
            }  # Matches colors between front & rear
        }

        # Step 7: Optical-Flow Seam Refinement
        self.optical_flow = {
            "enabled": True,
            "algorithm": "DISOpticalFlow",  # Options: DISOpticalFlow, calcOpticalFlowFarneback
            "flow_preset": "PRESET_FAST",
            "pyr_scale": 0.5,
            "levels": 3,
            "winsize": 15,
            "iterations": 3,
            "poly_n": 5,
            "poly_sigma": 1.2
        }

    def to_dict(self):
        """Serialize calibration parameters to dictionary.

        Returns:
            Dictionary containing all calibration metadata, lens parameters,
            transforms, maps, seam settings, and color compensation settings.
        """
        return {
            "metadata": self.metadata,
            "front_lens": self.front_lens,
            "rear_lens": self.rear_lens,
            "stereo_transform": self.stereo_transform,
            "projection_map": self.projection_map,
            "seam_and_blend": self.seam_and_blend,
            "color_compensation": self.color_compensation,
            "optical_flow": self.optical_flow
        }

    @classmethod
    def from_dict(cls, data):
        """Load calibration instance from a serialized parameter dictionary.

        Args:
            data: Dictionary containing calibration configuration dictionaries.

        Returns:
            Populated DualFisheyeCalibration instance.
        """
        calib = cls()
        calib.metadata = data.get("metadata", calib.metadata)
        calib.front_lens = data.get("front_lens", calib.front_lens)
        calib.rear_lens = data.get("rear_lens", calib.rear_lens)
        calib.stereo_transform = data.get("stereo_transform", calib.stereo_transform)
        calib.projection_map = data.get("projection_map", calib.projection_map)
        calib.seam_and_blend = data.get("seam_and_blend", calib.seam_and_blend)
        calib.color_compensation = data.get("color_compensation", calib.color_compensation)
        calib.optical_flow = data.get("optical_flow", calib.optical_flow)
        return calib

    def save_json(self, file_path):
        """Save calibration data to JSON file with Linux EOL.

        Args:
            file_path: Destination path for the exported JSON file.
        """
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", newline="\n", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        print(f"[SUCCESS] Calibration data saved to '{file_path}'")

    @classmethod
    def load_json(cls, file_path):
        """Load calibration data from a JSON file.

        Args:
            file_path: Path to the calibration JSON file to load.

        Returns:
            Populated DualFisheyeCalibration instance.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[SUCCESS] Loaded calibration data from '{file_path}'")
        return cls.from_dict(data)


# OpenCV-based Calibration & Pipeline Step Functions
def calibrate_single_lens(image_paths, pattern_size=(9, 6), square_size_mm=25.0):
    """Recover single lens Kannala-Brandt calibration using OpenCV fisheye model.

    Args:
        image_paths: Sequence of filepaths to calibration chessboard images.
        pattern_size: Chessboard internal corner dimensions (cols, rows).
        square_size_mm: Physical size of calibration chessboard squares in millimeters.

    Returns:
        Dictionary containing camera intrinsic matrix 'K', distortion
        coefficients 'D', center 'cx', 'cy', focal lengths 'fx', 'fy',
        and calibration root mean square reprojection error 'rms'.

    Raises:
        RuntimeError: If OpenCV or NumPy is not installed.
        ValueError: If no chessboard corners could be detected in the images.
    """
    if not HAS_OPENCV:
        raise RuntimeError("OpenCV (cv2) and NumPy are required for calibration processing.")

    board_w, board_h = pattern_size
    objp = np.zeros((1, board_w * board_h, 3), np.float32)
    objp[0, :, :2] = np.mgrid[0:board_w, 0:board_h].T.reshape(-1, 2) * square_size_mm

    objpoints = []  # 3d point in real world space
    imgpoints = []  # 2d points in image plane.

    img_dim = None

    for p in image_paths:
        img = cv2.imread(p)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if img_dim is None:
            img_dim = (gray.shape[1], gray.shape[0])

        ret, corners = cv2.findChessboardCorners(
            gray, pattern_size,
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_FAST_CHECK + cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        if ret:
            subpix_corners = cv2.cornerSubPix(
                gray, corners, (3, 3), (-1, -1),
                (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1)
            )
            objpoints.append(objp)
            imgpoints.append(subpix_corners.reshape(1, -1, 2))

    if len(objpoints) == 0:
        raise ValueError("No chessboard corners found in provided images.")

    N_OK = len(objpoints)
    K = np.zeros((3, 3), dtype=np.float64)
    D = np.zeros((4, 1), dtype=np.float64)
    rvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in range(N_OK)]
    tvecs = [np.zeros((1, 1, 3), dtype=np.float64) for _ in range(N_OK)]

    calibration_flags = cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC + cv2.fisheye.CALIB_FIX_SKEW

    rms, K, D, rvecs, tvecs = cv2.fisheye.calibrate(
        objpoints, imgpoints, img_dim, K, D, rvecs, tvecs,
        calibration_flags, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-6)
    )

    return {
        "K": K.tolist(),
        "D": D.flatten().tolist(),
        "cx": K[0, 2],
        "cy": K[1, 2],
        "fx": K[0, 0],
        "fy": K[1, 1],
        "rms": float(rms)
    }


def calibrate_stereo_pair(front_imgs, rear_imgs, pattern_size=(9, 6), square_size_mm=25.0, K1=None, D1=None, K2=None, D2=None):
    """Estimate lens-to-lens stereo transform using cv2.fisheye.stereoCalibrate.

    Args:
        front_imgs: Sequence of filepaths to front lens calibration images.
        rear_imgs: Sequence of filepaths to rear lens calibration images.
        pattern_size: Chessboard internal corner dimensions (cols, rows).
        square_size_mm: Physical size of calibration chessboard squares in millimeters.
        K1: Front camera 3x3 intrinsic matrix.
        D1: Front camera fisheye distortion coefficients.
        K2: Rear camera 3x3 intrinsic matrix.
        D2: Rear camera fisheye distortion coefficients.

    Returns:
        Dictionary containing rotation matrix 'R', rotation vector 'rvec',
        translation vector 'T', and stereo calibration RMS error 'stereo_rms'.

    Raises:
        RuntimeError: If OpenCV or NumPy is not installed.
        ValueError: If no matching stereo corners are found between image pairs.
    """
    if not HAS_OPENCV:
        raise RuntimeError("OpenCV (cv2) and NumPy are required for stereo calibration.")

    # Convert inputs to numpy arrays
    K1_arr = np.array(K1, dtype=np.float64)
    D1_arr = np.array(D1, dtype=np.float64).reshape(4, 1)
    K2_arr = np.array(K2, dtype=np.float64)
    D2_arr = np.array(D2, dtype=np.float64).reshape(4, 1)

    board_w, board_h = pattern_size
    objp = np.zeros((1, board_w * board_h, 3), np.float32)
    objp[0, :, :2] = np.mgrid[0:board_w, 0:board_h].T.reshape(-1, 2) * square_size_mm

    objpoints = []
    imgpoints_front = []
    imgpoints_rear = []

    img_dim = None

    for p1, p2 in zip(front_imgs, rear_imgs):
        f_img = cv2.imread(p1)
        r_img = cv2.imread(p2)
        if f_img is None or r_img is None:
            continue
        g1 = cv2.cvtColor(f_img, cv2.COLOR_BGR2GRAY)
        g2 = cv2.cvtColor(r_img, cv2.COLOR_BGR2GRAY)
        if img_dim is None:
            img_dim = (g1.shape[1], g1.shape[0])

        ret1, c1 = cv2.findChessboardCorners(g1, pattern_size)
        ret2, c2 = cv2.findChessboardCorners(g2, pattern_size)

        if ret1 and ret2:
            sub1 = cv2.cornerSubPix(g1, c1, (3, 3), (-1, -1), (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1))
            sub2 = cv2.cornerSubPix(g2, c2, (3, 3), (-1, -1), (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1))
            objpoints.append(objp)
            imgpoints_front.append(sub1.reshape(1, -1, 2))
            imgpoints_rear.append(sub2.reshape(1, -1, 2))

    if len(objpoints) == 0:
        raise ValueError("No matching stereo corners found.")

    R = np.eye(3, dtype=np.float64)
    T = np.zeros((3, 1), dtype=np.float64)

    flags = cv2.fisheye.CALIB_FIX_INTRINSIC

    rms, _, _, _, _, R, T = cv2.fisheye.stereoCalibrate(
        objpoints, imgpoints_front, imgpoints_rear,
        K1_arr, D1_arr, K2_arr, D2_arr, img_dim,
        R, T, flags, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-6)
    )

    rvec, _ = cv2.Rodrigues(R)

    return {
        "R": R.tolist(),
        "rvec": rvec.flatten().tolist(),
        "T": T.flatten().tolist(),
        "stereo_rms": float(rms)
    }


def main():
    """CLI entry point for exporting or inspecting dual-fisheye calibration parameters."""
    parser = argparse.ArgumentParser(description="Dual Fisheye 360 Camera Calibration Exporter")
    parser.add_argument("--save-sample", type=str, default="others/dual_fisheye_calibration.json",
                        help="Path to save a sample/default calibration JSON file")
    parser.add_argument("--info", type=str, help="Print summary of an existing calibration JSON file")
    args = parser.parse_args()

    if args.info:
        if not os.path.exists(args.info):
            print(f"[ERROR] File not found: {args.info}")
            sys.exit(1)
        calib = DualFisheyeCalibration.load_json(args.info)
        print("\n--- Calibration Summary ---")
        print(f"Name: {calib.metadata.get('name')}")
        print(f"Lens Model: {calib.metadata.get('lens_model')}")
        print(f"Front Lens Center (cx, cy): {calib.front_lens['optical_center']}")
        print(f"Rear Lens Center (cx, cy): {calib.rear_lens['optical_center']}")
        print(f"Stereo Translation (T): {calib.stereo_transform['translation_vector_T']}")
        print(f"Seam Finder: {calib.seam_and_blend['seam_finder']}")
        print(f"Exposure Gain (Front/Rear): {calib.color_compensation['front_exposure_gain']} / {calib.color_compensation['rear_exposure_gain']}")
        return

    # Default action: Export complete calibration file
    calib = DualFisheyeCalibration(name="Gear360_DualFisheye_Calibrated", width=3840, height=1920)
    calib.save_json(args.save_sample)


if __name__ == "__main__":
    main()
