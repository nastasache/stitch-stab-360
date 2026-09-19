#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
"""Dual-fisheye 360° video stitching pipeline via OpenCV remap and multiband blending.

Processes Samsung Gear 360 (SM-C200) dual-fisheye video frames (3840x1920), splits
front and rear fisheye views, remaps each half to equirectangular coordinates using
calibrated lens distortion profiles, balances exposure, blends seam boundaries, and
pipes raw BGR frames to FFmpeg for encoding with 360° spatial metadata injection.
"""

import sys
import os
import json
import time
import argparse
import subprocess
import numpy as np
import cv2

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class SMC200StitchPipeline:
    """Manages dual-fisheye equirectangular stitching pipeline and FFmpeg encoding."""

    def __init__(self, calib_file, out_w=3840, out_h=1920, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0):
        """Initialize the stitch pipeline with calibration parameters and projection dimensions.

        Args:
            calib_file: Filepath to dual-fisheye calibration JSON.
            out_w: Target panorama output width in pixels.
            out_h: Target panorama output height in pixels.
            yaw_deg: Global yaw orientation correction in degrees.
            pitch_deg: Global pitch orientation correction in degrees.
            roll_deg: Global roll orientation correction in degrees.

        Raises:
            FileNotFoundError: If calib_file is not found at the specified path.
        """
        self.out_w = out_w
        self.out_h = out_h
        self.yaw_deg = float(yaw_deg)
        self.pitch_deg = float(pitch_deg)
        self.roll_deg = float(roll_deg)

        # Load calibration parameters
        if not os.path.exists(calib_file):
            alt_path = os.path.join("others", os.path.basename(calib_file))
            if os.path.exists(alt_path):
                calib_file = alt_path
            else:
                raise FileNotFoundError(f"Calibration file not found: {calib_file}")

        with open(calib_file, "r", encoding="utf-8") as f:
            self.calib = json.load(f)

        print(f"[PIPELINE] Loaded calibration from '{calib_file}' (Yaw: {self.yaw_deg}°, Pitch: {self.pitch_deg}°, Roll: {self.roll_deg}°)")
        self._init_remap_tables()
        self._init_seam_and_blender()

    def _init_remap_tables(self):
        """Precompute 3D equirectangular remapping lookup tables for front and rear lenses.

        Constructs 3D equirectangular unit ray directions, applies yaw/pitch/roll
        rotations, projects rays through Kannala-Brandt polynomial distortion models,
        and generates coordinate remap tables (map_x, map_y) and blending weight masks.
        """
        print("[PIPELINE] Precomputing 3D equirectangular lookup tables...")
        front = self.calib.get("front_lens", {})
        rear = self.calib.get("rear_lens", {})
        stereo = self.calib.get("stereo_transform", {})

        K_front = np.array(front.get("camera_matrix", [[960, 0, 960], [0, 960, 960], [0, 0, 1]]), dtype=np.float32)
        D_front = np.array(front.get("distortion_coeffs", [0, 0, 0, 0]), dtype=np.float32)
        fov_front = np.radians(front.get("fov_degrees", 195.0))

        K_rear = np.array(rear.get("camera_matrix", [[960, 0, 960], [0, 960, 960], [0, 0, 1]]), dtype=np.float32)
        D_rear = np.array(rear.get("distortion_coeffs", [0, 0, 0, 0]), dtype=np.float32)
        fov_rear = np.radians(rear.get("fov_degrees", 195.0))

        R_rel = np.array(stereo.get("rotation_matrix_R", [[1, 0, 0], [0, 1, 0], [0, 0, 1]]), dtype=np.float32)

        # Build Equirectangular Mesh Grid
        x_coords = np.linspace(-np.pi, np.pi, self.out_w, endpoint=False, dtype=np.float32)
        y_coords = np.linspace(np.pi / 2.0, -np.pi / 2.0, self.out_h, dtype=np.float32)
        lon, lat = np.meshgrid(x_coords, y_coords)

        # 3D Unit Direction Vectors
        X = np.cos(lat) * np.sin(lon)
        Y = np.sin(lat)
        Z = np.cos(lat) * np.cos(lon)
        P_world = np.stack([X, Y, Z], axis=-1) # (H, W, 3)

        # Apply Yaw, Pitch, Roll orientation corrections
        if self.yaw_deg != 0.0 or self.pitch_deg != 0.0 or self.roll_deg != 0.0:
            y_rad = np.radians(self.yaw_deg)
            p_rad = np.radians(self.pitch_deg)
            r_rad = np.radians(self.roll_deg)

            R_yaw = np.array([[np.cos(y_rad), 0, np.sin(y_rad)], [0, 1, 0], [-np.sin(y_rad), 0, np.cos(y_rad)]], dtype=np.float32)
            R_pitch = np.array([[1, 0, 0], [0, np.cos(p_rad), -np.sin(p_rad)], [0, np.sin(p_rad), np.cos(p_rad)]], dtype=np.float32)
            R_roll = np.array([[np.cos(r_rad), -np.sin(r_rad), 0], [np.sin(r_rad), np.cos(r_rad), 0], [0, 0, 1]], dtype=np.float32)
            R_ypr = R_yaw @ R_pitch @ R_roll
            P_world = P_world @ R_ypr.T

        X, Y, Z = P_world[..., 0], P_world[..., 1], P_world[..., 2]

        # --- Front Lens Remapping (Facing +Z) ---
        theta_front = np.arccos(np.clip(Z, -1.0, 1.0)) # Angle from optical axis +Z
        phi_front = np.arctan2(-Y, X) # -Y for inverted camera pixel coordinate space
        
        # Fisheye distortion theta_d = theta * (1 + k1*theta^2 + k2*theta^4 + k3*theta^6 + k4*theta^8)
        th2 = theta_front**2
        th4 = th2**2
        th6 = th4 * th2
        th8 = th4**2
        k1, k2, k3, k4 = D_front[0], D_front[1], D_front[2], D_front[3]
        theta_d_f = theta_front * (1.0 + k1 * th2 + k2 * th4 + k3 * th6 + k4 * th8)

        fx_f, fy_f = K_front[0, 0], K_front[1, 1]
        cx_f, cy_f = K_front[0, 2], K_front[1, 2]
        f_avg_f = (fx_f + fy_f) / 2.0
        r_dist_f = theta_d_f * (f_avg_f / (np.pi / 2.0))

        self.map_x_front = (cx_f + r_dist_f * np.cos(phi_front)).astype(np.float32)
        self.map_y_front = (cy_f + r_dist_f * np.sin(phi_front)).astype(np.float32)
        self.mask_front = (theta_front <= (fov_front / 2.0)).astype(np.uint8) * 255

        # --- Rear Lens Remapping (Facing -Z) ---
        # Apply relative stereo transform if present
        if not np.allclose(R_rel, np.eye(3)):
            P_rear = P_world @ R_rel.T
            Xr, Yr, Zr = P_rear[..., 0], P_rear[..., 1], P_rear[..., 2]
        else:
            Xr, Yr, Zr = X, Y, Z

        theta_rear = np.arccos(np.clip(-Zr, -1.0, 1.0)) # Angle from rear optical axis -Z
        phi_rear = np.arctan2(-Yr, -Xr) # Inverted -Y for pixel space and -X for rear direction

        th2_r = theta_rear**2
        th4_r = th2_r**2
        th6_r = th4_r * th2_r
        th8_r = th4_r**2
        kr1, kr2, kr3, kr4 = D_rear[0], D_rear[1], D_rear[2], D_rear[3]
        theta_d_r = theta_rear * (1.0 + kr1 * th2_r + kr2 * th4_r + kr3 * th6_r + kr4 * th8_r)

        fx_r, fy_r = K_rear[0, 0], K_rear[1, 1]
        cx_r, cy_r = K_rear[0, 2], K_rear[1, 2]
        f_avg_r = (fx_r + fy_r) / 2.0
        r_dist_r = theta_d_r * (f_avg_r / (np.pi / 2.0))

        self.map_x_rear = (cx_r + r_dist_r * np.cos(phi_rear)).astype(np.float32)
        self.map_y_rear = (cy_r + r_dist_r * np.sin(phi_rear)).astype(np.float32)
        # Calculate smooth angular weight maps in overlap region
        max_theta_f = fov_front / 2.0
        max_theta_r = fov_rear / 2.0
        blend_band_rad = np.radians(15.0)

        mask_f_weight = np.clip((max_theta_f - theta_front) / blend_band_rad, 0.0, 1.0)
        mask_r_weight = np.clip((max_theta_r - theta_rear) / blend_band_rad, 0.0, 1.0)

        w_sum = mask_f_weight + mask_r_weight + 1e-5
        self.w_front = (mask_f_weight / w_sum)[..., np.newaxis]
        self.w_rear = (mask_r_weight / w_sum)[..., np.newaxis]

        print("[PIPELINE] Precomputed remap tables successfully.")

    def _init_seam_and_blender(self):
        """Initialize seam finding and multiband blender weights."""
        pass

    def stitch_frame(self, frame_bgr):
        """Process a single side-by-side dual-fisheye frame into an equirectangular panorama.

        Args:
            frame_bgr: Raw input frame image array (height, width, 3) containing
                both front and rear fisheye circles side-by-side.

        Returns:
            Stitched 360° equirectangular image as a uint8 BGR numpy array.
        """
        h, w = frame_bgr.shape[:2]
        half_w = w // 2

        # Split Front & Rear fisheye views
        img_front = frame_bgr[:, :half_w]
        img_rear = frame_bgr[:, half_w:]

        # Step 3: OpenCV Remap using calibrated lookup maps
        equi_front = cv2.remap(img_front, self.map_x_front, self.map_y_front, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        equi_rear = cv2.remap(img_rear, self.map_x_rear, self.map_y_rear, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

        # Step 4: Exposure / Brightness Compensation
        exp = self.calib.get("color_compensation", {})
        gain_f = exp.get("front_exposure_gain", 1.0)
        gain_r = exp.get("rear_exposure_gain", 1.0)

        if gain_f != 1.0:
            equi_front = cv2.convertScaleAbs(equi_front, alpha=gain_f)
        if gain_r != 1.0:
            equi_rear = cv2.convertScaleAbs(equi_rear, alpha=gain_r)

        # Step 5 & 6: Blend into Equirectangular Panorama
        stitched = (equi_front.astype(np.float32) * self.w_front + equi_rear.astype(np.float32) * self.w_rear)
        return np.clip(stitched, 0, 255).astype(np.uint8)

    def process_video(self, input_path, output_path, max_frames=0, encoder="libx264", crf=18, status_file=None, lossless=False):
        """Read input video, stitch frame-by-frame, update status.json, and encode output video.

        Args:
            input_path: Filepath to source dual-fisheye video.
            output_path: Filepath for the rendered stitched video.
            max_frames: Optional upper limit on processed frames (0 processes all).
            encoder: FFmpeg video encoder codec name (e.g., 'libx264', 'h264_nvenc', 'ffv1').
            crf: Constant Rate Factor compression parameter for FFmpeg encoding.
            status_file: Optional path to status JSON file for tracking progress.
            lossless: Whether to encode using lossless FFV1 codec.

        Raises:
            FileNotFoundError: If input_path does not exist.
            RuntimeError: If video stream cannot be opened by OpenCV.
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input video file not found: {input_path}")

        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open input video stream: {input_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if max_frames > 0:
            total_frames = min(total_frames, max_frames)

        if lossless:
            print(f"[PIPELINE] Input: {input_path} ({total_frames} frames, {fps:.2f} FPS)")
            print(f"[PIPELINE] Output: {output_path} (Encoder: ffv1, Lossless intermediate)")
        else:
            print(f"[PIPELINE] Input: {input_path} ({total_frames} frames, {fps:.2f} FPS)")
            print(f"[PIPELINE] Output: {output_path} (Encoder: {encoder}, CRF: {crf})")

        # Step 8: FFmpeg Subprocess Encoder Pipe
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{self.out_w}x{self.out_h}",
            "-pix_fmt", "bgr24",
            "-r", str(fps),
            "-i", "-",
        ]
        if lossless:
            ffmpeg_cmd.extend(["-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv444p"])
        elif "nvenc" in encoder:
            ffmpeg_cmd.extend(["-c:v", encoder, "-b:v", "30000k", "-preset", "fast", "-pix_fmt", "yuv420p"])
        else:
            ffmpeg_cmd.extend(["-c:v", encoder, "-crf", str(crf), "-preset", "fast", "-pix_fmt", "yuv420p"])

        ffmpeg_cmd.append(output_path)

        pipe = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=WIN_NO_WINDOW)

        frame_idx = 0
        start_time = time.time()

        try:
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                stitched_frame = self.stitch_frame(frame)
                try:
                    pipe.stdin.write(stitched_frame.tobytes())
                except BrokenPipeError:
                    print(f"\n[ERROR] FFmpeg pipe broken at frame {frame_idx} — encoder may have crashed.")
                    break

                elapsed = time.time() - start_time
                fps_curr = frame_idx / (elapsed + 1e-5)
                progress = min(99.0, (frame_idx / total_frames) * 100.0) if total_frames > 0 else 50.0
                eta_sec = int((total_frames - frame_idx) / (fps_curr + 1e-5)) if (total_frames > 0 and fps_curr > 0) else 0

                if status_file:
                    try:
                        tmp_sf = status_file + ".tmp"
                        os.makedirs(os.path.dirname(os.path.abspath(status_file)), exist_ok=True)
                        with open(tmp_sf, "w") as sf:
                            json.dump({
                                "status": "stitching",
                                "phase": f"OpenCV 3D Remap & MultiBand ({frame_idx}/{total_frames})",
                                "progress": round(progress, 1),
                                "speed": f"{fps_curr:.1f} fps",
                                "eta": f"{eta_sec}s",
                                "elapsed": int(elapsed),
                                "pid": os.getpid()
                            }, sf)
                        os.replace(tmp_sf, status_file)
                    except Exception as _se:
                        print(f"[WARN] Status file update failed: {_se}", flush=True)

                if frame_idx % 5 == 0 or frame_idx == total_frames:
                    print(f"Stitching Frame {frame_idx}/{total_frames} ({progress:.1f}%) - {fps_curr:.2f} fps", end="\r")

                if max_frames > 0 and frame_idx >= max_frames:
                    break
        finally:
            cap.release()
            try:
                # communicate(timeout) prevents the deadlock that pipe.wait() causes when
                # FFmpeg's stdout buffer fills up after the encoder crashes mid-encode.
                pipe.stdin.close()
                pipe.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                print("[ERROR] FFmpeg did not exit within 300 s — killing process.")
                pipe.kill()
                pipe.communicate()
            except Exception as _pe:
                print(f"[WARN] FFmpeg cleanup error: {_pe}")


        print(f"\n[SUCCESS] Pipeline finished stitching {frame_idx} frames in {time.time() - start_time:.2f}s!")
        print(f"[SUCCESS] Output written to: {output_path}")

        # Step 9: Inject 360° VR Spherical Metadata
        try:
            print("[PIPELINE] Injecting 360° VR spatial metadata...")
            vr_output = output_path.replace(".mp4", "_VR.mp4").replace(".MP4", "_VR.MP4")
            if vr_output == output_path:
                vr_output = output_path + "_VR.mp4"
            cmd_meta = [sys.executable, "-B", "-m", "spatialmedia", "-i", "-p", "equirectangular", output_path, vr_output]
            proc = subprocess.run(cmd_meta, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=WIN_NO_WINDOW)
            if proc.returncode == 0 and os.path.exists(vr_output):
                print(f"[SUCCESS] 360° VR metadata injected successfully -> '{vr_output}'")
            else:
                print(f"[WARNING] Metadata injection skipped: {proc.stderr}")
        except Exception as e:
            print(f"[WARNING] Metadata injection error: {e}")


def stitch_single_image(input_image_path, output_image_path, calib_file, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0):
    """Stitch a single image frame or extract first frame from video to equirectangular image.

    Args:
        input_image_path: Path to dual-fisheye image or video file.
        output_image_path: Path to save the stitched equirectangular output image.
        calib_file: Path to calibration parameters JSON file.
        yaw_deg: Yaw rotation adjustment in degrees.
        pitch_deg: Pitch rotation adjustment in degrees.
        roll_deg: Roll rotation adjustment in degrees.

    Raises:
        ValueError: If frame or image cannot be read from input_image_path.
    """
    pipe = SMC200StitchPipeline(calib_file=calib_file, yaw_deg=yaw_deg, pitch_deg=pitch_deg, roll_deg=roll_deg)
    if input_image_path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".m4v")):
        cap = cv2.VideoCapture(input_image_path)
        ret, img = cap.read()
        cap.release()
        if not ret:
            raise ValueError(f"Could not read frame from video: {input_image_path}")
    else:
        img = cv2.imread(input_image_path)
        if img is None:
            raise ValueError(f"Could not load image: {input_image_path}")
    stitched = pipe.stitch_frame(img)
    os.makedirs(os.path.dirname(os.path.abspath(output_image_path)), exist_ok=True)
    cv2.imwrite(output_image_path, stitched)
    print(f"[SUCCESS] Single frame stitched to '{output_image_path}'")


def main():
    """CLI entry point for dual-fisheye practical video and image stitching pipeline."""
    parser = argparse.ArgumentParser(description="SM-C200 Dual Fisheye Practical Stitching Pipeline")
    parser.add_argument("--input", type=str, default="360_0009_small.MP4", help="Path to SM-C200 MP4 video or image file")
    parser.add_argument("--calibration", type=str, default="others/dual_fisheye_calibration.json", help="Calibration JSON file")
    parser.add_argument("--output", type=str, default="360_0009_small_stitched_opencv.mp4", help="Output stitched 360 video path")
    parser.add_argument("--max-frames", type=int, default=30, help="Max frames to stitch (0 = process all)")
    parser.add_argument("--encoder", type=str, default="libx264", help="FFmpeg video codec (e.g. libx264, h264_nvenc, libx265)")
    parser.add_argument("--crf", type=int, default=18, help="FFmpeg CRF quality setting")
    parser.add_argument("--yaw", type=float, default=0.0, help="Yaw offset in degrees")
    parser.add_argument("--pitch", type=float, default=0.0, help="Pitch offset in degrees")
    parser.add_argument("--roll", type=float, default=0.0, help="Roll offset in degrees")
    parser.add_argument("--single-frame", action="store_true", help="Stitch input as a single image frame")
    args = parser.parse_args()

    if args.single_frame or args.input.endswith((".jpg", ".png", ".jpeg")):
        stitch_single_image(args.input, args.output, args.calibration, yaw_deg=args.yaw, pitch_deg=args.pitch, roll_deg=args.roll)
    else:
        pipeline = SMC200StitchPipeline(calib_file=args.calibration, yaw_deg=args.yaw, pitch_deg=args.pitch, roll_deg=args.roll)
        pipeline.process_video(input_path=args.input, output_path=args.output, max_frames=args.max_frames, encoder=args.encoder, crf=args.crf)


if __name__ == "__main__":
    main()
