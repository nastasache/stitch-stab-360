#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
"""Diagnostic plotting and subtitle overlay generator for 360 video stabilization.

Visualizes IMU telemetry corrections and optical transform series (VidSTAB,
Kabsch, Kopf, Cinematic, Travel-Direction), rendering high-resolution PNG
diagnostic graphs (raw motion vs. applied correction vs. stabilized output) and
generating SSA/ASS subtitles for in-player real-time verification.
"""

import sys
import re
import os
import json
import argparse


# -------------------------------------------------------------
# Parsing
# -------------------------------------------------------------

def parse_sendcmd(filepath):
    """Parse an FFmpeg sendcmd parameter command file.

    Extracts frame timestamp intervals and rotation angles (yaw, pitch, roll).

    Args:
        filepath: Filepath to sendcmd text file.

    Returns:
        List of tuples formatted as (start_time_sec, param_name, param_value).
    """
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(
                r"^([\d.]+)-([\d.]+)\s+\[enter\]\s+v360\s+(\w+)\s+([-\d.]+)",
                line
            )
            if m:
                start_t = float(m.group(1))
                param   = m.group(3)
                value   = float(m.group(4))
                entries.append((start_t, param, value))
            elif "[enter]" in line:
                parts = line.split("[enter]")
                t_str = parts[0].strip().split("-")[0].strip()
                try:
                    start_t = float(t_str)
                    for match in re.finditer(r"(yaw|pitch|roll)\s+([-\d.]+)", parts[1]):
                        entries.append((start_t, match.group(1), float(match.group(2))))
                except (ValueError, IndexError):
                    pass
    return entries


def group_by_frame(entries):
    """Group parsed sendcmd entries by start timestamp into orientation parameter maps.

    Args:
        entries: Sequence of (start_time_sec, param_name, param_value) tuples.

    Returns:
        Tuple of (sorted_timestamps_list, parameter_map_by_time).
    """
    by_time = {}
    for t, param, value in entries:
        if t not in by_time:
            by_time[t] = {}
        by_time[t][param] = value
    times = sorted(by_time.keys())
    return times, by_time


# -------------------------------------------------------------
# Graph
# -------------------------------------------------------------

def generate_graph(times_l, by_time_l, times_r, by_time_r, output_png, fps=29.97, fusion_method="none", fusion_gain=0.5):
    """Generate dual-axis diagnostic plot of telemetry pitch and roll stabilization curves.

    Visualizes raw camera tilt, applied counter-rotation, and final stabilized
    horizon output across playback duration and frame indices.

    Args:
        times_l: Sequence of frame timestamps for left/front lens.
        by_time_l: Mapping of left timestamps to parameter values.
        times_r: Sequence of frame timestamps for right/rear lens.
        by_time_r: Mapping of right timestamps to parameter values.
        output_png: Target destination path for PNG chart.
        fps: Playback frame rate for frame number secondary axis.
        fusion_method: 6-Axis IMU sensor fusion filter used ('ekf', 'mahony', 'complementary', 'none').
        fusion_gain: Tuning gain or filter parameter for sensor fusion.

    Returns:
        True if graph was successfully created and saved, False otherwise.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        print("Warning: matplotlib not installed. Graph skipped.")
        print("  Install with: pip install matplotlib")
        return False

    fig, axes = plt.subplots(2, 1, figsize=(11, 7.0), sharex=True, facecolor="#1a1a2e")
    for ax in axes:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#cccccc")
        for spine in ax.spines.values():
            spine.set_color("#444444")

    raw_pitches_l = [by_time_l[t].get("pitch", 0.0) for t in times_l]
    raw_rolls_l   = [by_time_l[t].get("roll",  0.0) for t in times_l]

    # Compute cumulative correction curve (pitches_l, rolls_l) and moving average trend (dotted)
    import numpy as np
    pitches_l, rolls_l = [], []
    acc_p, acc_r = 0.0, 0.0
    for p, r in zip(raw_pitches_l, raw_rolls_l):
        acc_p += p
        acc_r += r
        pitches_l.append(acc_p)
        rolls_l.append(acc_r)

    win = max(3, min(15, len(pitches_l) // 10)) if len(pitches_l) > 10 else 1
    kernel = np.ones(win) / win
    pitches_trend = np.convolve(pitches_l, kernel, mode='same') if len(pitches_l) > win else pitches_l
    rolls_trend = np.convolve(rolls_l, kernel, mode='same') if len(rolls_l) > win else rolls_l

    fusion_label = ""
    f_m = str(fusion_method).lower() if fusion_method else "none"
    if f_m not in ("none", "disabled", "false", ""):
        fusion_label = f" [Sensor Fusion: {f_m.upper()} (Gain: {float(fusion_gain):.2f})]"
    else:
        fusion_label = " [Sensor Fusion: Disabled (Raw IMU)]"

    axes[0].set_title(f"Telemetry Stabilization{fusion_label}: Raw Motion vs Applied Correction vs Stabilized Output", color="#ffffff", fontsize=10.5, fontweight="bold", pad=28)

    # Top secondary X axis for Frame Numbers
    secax = axes[0].secondary_xaxis('top', functions=(lambda t: t * fps, lambda f: f / fps if fps else f))
    secax.set_xlabel(f"Frame Number (at {fps:.2f} fps)", color="#38bdf8", fontsize=9, fontweight="bold", labelpad=8)
    secax.tick_params(colors="#38bdf8")
    secax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=20, steps=[1, 2, 2.5, 5, 10]))

    pitches_arr = np.array(pitches_l)
    # Final stabilized horizon is the sum of raw camera tilt (-pitches_arr) and applied counter-rotation (pitches_arr)
    raw_pitch_tilt = -pitches_arr
    applied_pitch_counter = pitches_arr
    pitches_stab_out = raw_pitch_tilt + applied_pitch_counter

    # Pitch Curves: 1. Raw Tilt (-pitches_arr), 2. Applied Counter-Rotation (pitches_arr), 3. Final Stabilized Output Horizon (0° baseline)
    axes[0].plot(times_l, raw_pitch_tilt, color="#ff1744", linewidth=1.2, linestyle="--", label="Raw cam", alpha=0.7)
    axes[0].plot(times_l, applied_pitch_counter, color="#00e5ff", linewidth=1.4, label="Correction", alpha=0.85)
    axes[0].plot(times_l, pitches_stab_out, color="#00e676", linewidth=1.8, label="Final", alpha=1.0)

    # Significant Peak Spike Detection for Pitch (mark the most important one spike/correction)
    if len(applied_pitch_counter) > 0 and np.max(np.abs(applied_pitch_counter)) > 1e-4:
        idx_p = int(np.argmax(np.abs(applied_pitch_counter)))
        val_p = applied_pitch_counter[idx_p]
        t_p = times_l[idx_p]
        f_p = int(round(t_p * fps))
        axes[0].scatter([t_p], [val_p], color="#ffea00", s=30, zorder=5)
        offset_pts = 16 if val_p >= 0 else -18
        va = "bottom" if val_p >= 0 else "top"
        lbl_p = f"{f_p}: {val_p:+.3f}" if abs(val_p) < 0.1 else f"{f_p}: {val_p:+.2f}"
        axes[0].annotate(lbl_p, xy=(t_p, val_p), xytext=(0, offset_pts),
                         textcoords="offset points", ha="center", va=va,
                         fontsize=8, color="#ffea00", fontweight="bold",
                         arrowprops=dict(arrowstyle="->", color="#ffea00", lw=0.8, shrinkA=2, shrinkB=2))

    if times_r:
        raw_pitches_r = [by_time_r[t].get("pitch", 0.0) for t in times_r]
        acc_pr = 0.0
        pitches_r = []
        for p in raw_pitches_r:
            acc_pr += p
            pitches_r.append(acc_pr)
        axes[0].plot(times_r, pitches_r, color="#FF5722", linewidth=1.0, linestyle="--", label="Right lens pitch", alpha=0.8)

    axes[0].axhline(0, color="#ffffff", linewidth=0.8, linestyle=":")
    axes[0].set_ylabel("Pitch Angle (deg)", color="#cccccc")
    axes[0].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper left", bbox_to_anchor=(1.02, 1.0))
    axes[0].grid(True, alpha=0.15, color="#888")

    rolls_arr = np.array(rolls_l)
    raw_roll_tilt = -rolls_arr
    applied_roll_counter = rolls_arr
    rolls_stab_out = raw_roll_tilt + applied_roll_counter

    # Roll Curves: 1. Raw Tilt (-rolls_arr), 2. Applied Counter-Rotation (rolls_arr), 3. Final Stabilized Output Horizon (0° baseline)
    axes[1].plot(times_l, raw_roll_tilt, color="#ff1744", linewidth=1.2, linestyle="--", label="Raw cam", alpha=0.7)
    axes[1].plot(times_l, applied_roll_counter, color="#00e5ff", linewidth=1.4, label="Correction", alpha=0.85)
    axes[1].plot(times_l, rolls_stab_out, color="#00e676", linewidth=1.8, label="Final", alpha=1.0)

    # Significant Peak Spike Detection for Roll (mark the most important one spike/correction)
    if len(applied_roll_counter) > 0 and np.max(np.abs(applied_roll_counter)) > 1e-4:
        idx_r = int(np.argmax(np.abs(applied_roll_counter)))
        val_r = applied_roll_counter[idx_r]
        t_r = times_l[idx_r]
        f_r = int(round(t_r * fps))
        axes[1].scatter([t_r], [val_r], color="#ffea00", s=30, zorder=5)
        offset_pts = 16 if val_r >= 0 else -18
        va = "bottom" if val_r >= 0 else "top"
        lbl_r = f"{f_r}: {val_r:+.3f}" if abs(val_r) < 0.1 else f"{f_r}: {val_r:+.2f}"
        axes[1].annotate(lbl_r, xy=(t_r, val_r), xytext=(0, offset_pts),
                         textcoords="offset points", ha="center", va=va,
                         fontsize=8, color="#ffea00", fontweight="bold",
                         arrowprops=dict(arrowstyle="->", color="#ffea00", lw=0.8, shrinkA=2, shrinkB=2))

    if times_r:
        raw_rolls_r = [by_time_r[t].get("roll", 0.0) for t in times_r]
        acc_rr = 0.0
        rolls_r = []
        for r in raw_rolls_r:
            acc_rr += r
            rolls_r.append(acc_rr)
        axes[1].plot(times_r, rolls_r, color="#FF9800", linewidth=1.0, linestyle="--", label="Right lens roll", alpha=0.8)

    axes[1].axhline(0, color="#ffffff", linewidth=0.8, linestyle=":")
    axes[1].set_ylabel("Roll Angle (deg)", color="#cccccc")
    axes[1].set_xlabel("Time (seconds)", color="#cccccc", labelpad=8)
    axes[1].xaxis.set_major_locator(ticker.MaxNLocator(nbins=20, steps=[1, 2, 2.5, 5, 10]))
    axes[1].xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, pos: f"{x:g}"))
    axes[1].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper left", bbox_to_anchor=(1.02, 1.0))
    axes[1].grid(True, alpha=0.15, color="#888")

    plt.tight_layout()
    plt.savefig(output_png, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"[OK] Enhanced graph saved with peak annotations: {output_png}")
    return True


# -------------------------------------------------------------
# ASS subtitle overlay
# -------------------------------------------------------------

# Note: &H colour codes use ASS/VSFilter ABGR byte order (Alpha, Blue, Green, Red)
ASS_HEADER = (
    "[Script Info]\n"
    "Title: Stabilization Debug Overlay\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 3840\n"
    "PlayResY: 1920\n"
    "Timer: 100.0000\n"
    "WrapStyle: 0\n"
    "\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
    " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
    " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Dbg,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,"
    "0,0,0,0,100,100,0,0,1,3,0,7,40,40,40,1\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def sec_to_ass(sec):
    """Convert float seconds to Advanced SubStation Alpha timestamp format (H:MM:SS.cs).

    Args:
        sec: Timestamp in fractional seconds.

    Returns:
        Formatted ASS time string.
    """
    h  = int(sec // 3600)
    m  = int((sec % 3600) // 60)
    s  = int(sec % 60)
    cs = int(round((sec % 1.0) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_ass(times, by_time, output_ass, fps=29.97, label="Left"):
    """Write an ASS subtitle file displaying real-time pitch, roll, and yaw angles per frame.

    Args:
        times: Sequence of frame start timestamps in seconds.
        by_time: Mapping of timestamps to orientation parameters.
        output_ass: Destination filepath for generated ASS subtitle file.
        fps: Playback frame rate used for frame duration calculation.
        label: Lens or stage identifier displayed in the overlay.

    Returns:
        True upon successful file generation.
    """
    frame_dur = 1.0 / fps
    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        for i, t in enumerate(times):
            end_t = times[i + 1] if i + 1 < len(times) else t + frame_dur
            p   = by_time[t].get("pitch", 0.0)
            r   = by_time[t].get("roll",  0.0)
            yaw = by_time[t].get("yaw",   0.0)
            text = (
                f"[{label}]  "
                f"Pitch: {p:+.2f} deg    "
                f"Roll: {r:+.2f} deg    "
                f"Yaw-delta: {yaw:+.2f} deg"
            )
            f.write(
                f"Dialogue: 0,"
                f"{sec_to_ass(t)},"
                f"{sec_to_ass(end_t)},"
                f"Dbg,,0,0,0,,{text}\n"
            )
    print(f"[OK] ASS overlay saved: {output_ass}")
    return True


# -------------------------------------------------------------
# Stats summary
# -------------------------------------------------------------

def print_stats(label, times, by_time):
    """Print statistical min/max/mean pitch and roll angles to terminal stdout.

    Args:
        label: Descriptive label for the sensor stream (e.g., 'Left' or 'Right').
        times: Sequence of frame timestamps.
        by_time: Mapping of timestamps to parameter dictionaries.
    """
    if not times:
        return
    pitches = [by_time[t].get("pitch", 0.0) for t in times]
    rolls   = [by_time[t].get("roll",  0.0) for t in times]
    print(f"\n  {label} lens statistics:")
    print(f"    Pitch  min={min(pitches):+.2f}  max={max(pitches):+.2f}  mean={sum(pitches)/len(pitches):+.2f} deg")
    print(f"    Roll   min={min(rolls):+.2f}  max={max(rolls):+.2f}  mean={sum(rolls)/len(rolls):+.2f} deg")
    print(f"    Frames: {len(times)}  Duration: {times[-1]:.2f}s")


def parse_trf(filepath):
    """Parse VidStab TRF transforms or equirectangular sendcmd trajectory files.

    Args:
        filepath: Path to transform or sendcmd text file.

    Returns:
        Tuple of lists: (frame_indices, delta_yaw, delta_pitch, delta_roll).
    """
    frames, dx, dy, da = [], [], [], []
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    # Check if sendcmd format (e.g. 0.000000-0.033367 [enter] v360 yaw/pitch/roll ...)
    if any("[enter]" in line for line in lines[:30]):
        by_time = {}
        for line in lines:
            line = line.strip()
            if not line or "[enter]" not in line:
                continue
            parts = line.split("[enter]")
            time_str = parts[0].strip()
            try:
                t_start = float(time_str.split("-")[0].strip())
            except (ValueError, IndexError):
                continue
            cmd_part = parts[1].strip()
            t_key = round(t_start, 6)
            if t_key not in by_time:
                by_time[t_key] = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}

            found_any = False
            for match in re.finditer(r"(yaw|pitch|roll)\s+([-\d.]+)", cmd_part):
                p_name = match.group(1)
                p_val = float(match.group(2))
                by_time[t_key][p_name] = p_val
                found_any = True

            if not found_any:
                cmd_tokens = cmd_part.split()
                if len(cmd_tokens) >= 3 and cmd_tokens[0] == "v360":
                    param = cmd_tokens[1]
                    try:
                        val = float(cmd_tokens[2].rstrip(";,"))
                        by_time[t_key][param] = val
                    except ValueError:
                        pass

        sorted_times = sorted(by_time.keys())
        for idx, t in enumerate(sorted_times):
            frames.append(idx + 1)
            dx.append(by_time[t].get("yaw", 0.0))
            dy.append(by_time[t].get("pitch", 0.0))
            da.append(by_time[t].get("roll", 0.0))
        return frames, dx, dy, da

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 4:
            try:
                frm = int(float(parts[0]))
                x   = float(parts[1])
                y   = float(parts[2])
                a   = float(parts[3])
                frames.append(frm)
                dx.append(x)
                dy.append(y)
                da.append(a)
            except ValueError:
                continue
    return frames, dx, dy, da


def generate_optical_graph(frames, dx, dy, da, output_png, fps=29.97, model_name="Optical (VidSTAB)"):
    """Generate dual-axis diagnostic chart for optical stabilization transforms.

    Plots instantaneous frame-to-frame shifts and cumulative trajectory drift
    curves for yaw, pitch, and roll axes.

    Args:
        frames: Sequence of integer frame indices.
        dx: Horizontal / yaw shift values.
        dy: Vertical / pitch shift values.
        da: In-plane roll rotation angles.
        output_png: Target destination path for PNG chart.
        fps: Playback frame rate for time conversion secondary axis.
        model_name: Heading label identifying the stabilization algorithm.

    Returns:
        True if graph was successfully created and saved, False otherwise.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        print("Warning: matplotlib not installed. Graph skipped.")
        return False

    fig, axes = plt.subplots(2, 1, figsize=(11, 7.0), sharex=True, facecolor="#1a1a2e")
    for ax in axes:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#cccccc")
        for spine in ax.spines.values():
            spine.set_color("#444444")

    axes[0].set_title(f"{model_name} Stabilization: Motion Vectors vs Counter-Shift vs Stabilized Output", color="#ffffff", fontsize=12, fontweight="bold", pad=28)

    times = [f / fps if fps else f for f in frames]

    # Top secondary X axis for Frame Numbers
    secax = axes[0].secondary_xaxis('top', functions=(lambda t: t * fps, lambda f: f / fps if fps else f))
    secax.set_xlabel(f"Frame Number (at {fps:.2f} fps)", color="#38bdf8", fontsize=9, fontweight="bold", labelpad=8)
    secax.tick_params(colors="#38bdf8")
    secax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=20, steps=[1, 2, 2.5, 5, 10]))

    import numpy as np
    # dx, dy, da parsed from sendcmd are the APPLIED COUNTER-SHIFTS/ROTATIONS
    applied_dx = np.array(dx)
    applied_dy = np.array(dy)
    applied_da = np.array(da)

    # Raw Motion (un-stabilized motion before counter-rotation)
    raw_dx = -applied_dx
    raw_dy = -applied_dy
    raw_da = -applied_da

    win = max(3, min(15, len(dx) // 10)) if len(dx) > 10 else 1
    kernel = np.ones(win) / win
    raw_dx_smooth = np.convolve(raw_dx, kernel, mode='same') if len(raw_dx) > win else raw_dx
    raw_dy_smooth = np.convolve(raw_dy, kernel, mode='same') if len(raw_dy) > win else raw_dy
    raw_da_smooth = np.convolve(raw_da, kernel, mode='same') if len(raw_da) > win else raw_da

    # Residual Stabilized Output (exact diff = raw_motion + applied_counter_shift = 0 Baseline)
    dx_stab_out = raw_dx + applied_dx
    dy_stab_out = raw_dy + applied_dy
    da_stab_out = raw_da + applied_da

    # Translation Subplot: Raw Motion (Red Dashed), Applied Counter-Shift (Cyan Solid), Final Stabilized Output (Bright Green)
    axes[0].plot(times, raw_dx, color="#ff1744", linewidth=1.2, linestyle="--", label="Raw cam", alpha=0.7)
    axes[0].plot(times, applied_dx, color="#00e5ff", linewidth=1.4, label="Correction", alpha=0.85)
    axes[0].plot(times, dx_stab_out, color="#00e676", linewidth=1.8, label="Final", alpha=1.0)

    # Significant Peak Spike Detection for Translation (mark the most important one spike/correction)
    if len(applied_dx) > 0 and np.max(np.abs(applied_dx)) > 1e-4:
        idx_dx = int(np.argmax(np.abs(applied_dx)))
        val_dx = applied_dx[idx_dx]
        t_dx = times[idx_dx]
        f_dx = frames[idx_dx] if idx_dx < len(frames) else int(round(t_dx * fps))
        axes[0].scatter([t_dx], [val_dx], color="#ffea00", s=30, zorder=5)
        offset_pts = 16 if val_dx >= 0 else -18
        va = "bottom" if val_dx >= 0 else "top"
        lbl_dx = f"{f_dx}: {val_dx:+.3f}" if abs(val_dx) < 0.1 else f"{f_dx}: {val_dx:+.2f}"
        axes[0].annotate(lbl_dx, xy=(t_dx, val_dx), xytext=(0, offset_pts),
                         textcoords="offset points", ha="center", va=va,
                         fontsize=8, color="#ffea00", fontweight="bold",
                         arrowprops=dict(arrowstyle="->", color="#ffea00", lw=0.8, shrinkA=2, shrinkB=2))

    axes[0].axhline(0, color="#ffffff", linewidth=0.8, linestyle=":")
    axes[0].set_ylabel("Translation / Yaw Shift", color="#cccccc")
    axes[0].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper left", bbox_to_anchor=(1.02, 1.0))
    axes[0].grid(True, alpha=0.15, color="#888")

    # Rotation Subplot: Raw Motion (Red Dashed), Applied Counter-Rotation (Cyan Solid), Final Stabilized Output (Bright Green)
    axes[1].plot(times, raw_da, color="#ff1744", linewidth=1.2, linestyle="--", label="Raw cam", alpha=0.7)
    axes[1].plot(times, applied_da, color="#00e5ff", linewidth=1.4, label="Correction", alpha=0.85)
    axes[1].plot(times, da_stab_out, color="#00e676", linewidth=1.8, label="Final", alpha=1.0)

    # Significant Peak Spike Detection for Rotation (mark the most important one spike/correction)
    rot_series = applied_da
    if len(applied_da) > 0 and np.max(np.abs(applied_da)) < 1e-4 and len(applied_dy) > 0 and np.max(np.abs(applied_dy)) >= 1e-4:
        rot_series = applied_dy

    if len(rot_series) > 0 and np.max(np.abs(rot_series)) > 1e-4:
        idx_da = int(np.argmax(np.abs(rot_series)))
        val_da = rot_series[idx_da]
        t_da = times[idx_da]
        f_da = frames[idx_da] if idx_da < len(frames) else int(round(t_da * fps))
        axes[1].scatter([t_da], [val_da], color="#ffea00", s=30, zorder=5)
        offset_pts = 16 if val_da >= 0 else -18
        va = "bottom" if val_da >= 0 else "top"
        lbl_da = f"{f_da}: {val_da:+.3f}" if abs(val_da) < 0.1 else f"{f_da}: {val_da:+.2f}"
        axes[1].annotate(lbl_da, xy=(t_da, val_da), xytext=(0, offset_pts),
                         textcoords="offset points", ha="center", va=va,
                         fontsize=8, color="#ffea00", fontweight="bold",
                         arrowprops=dict(arrowstyle="->", color="#ffea00", lw=0.8, shrinkA=2, shrinkB=2))

    axes[1].axhline(0, color="#ffffff", linewidth=0.8, linestyle=":")
    axes[1].set_ylabel("Rotation / Pitch-Roll", color="#cccccc")
    axes[1].set_xlabel("Time (seconds)", color="#cccccc", labelpad=8)
    axes[1].xaxis.set_major_locator(ticker.MaxNLocator(nbins=20, steps=[1, 2, 2.5, 5, 10]))
    axes[1].xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, pos: f"{x:g}"))
    axes[1].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper left", bbox_to_anchor=(1.02, 1.0))
    axes[1].grid(True, alpha=0.15, color="#888")

    plt.tight_layout()
    plt.savefig(output_png, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"[OK] Enhanced graph saved for {model_name}: {output_png}")
    return True


def generate_traveldir_graph(
    times,
    raw_yaw,
    trend_yaw,
    locked_yaw,
    deadband_deg=1.5,
    damping=0.90,
    mode="travel_direction",
    output_png="traveldir_graph.png",
    fps=29.97
):
    """Generate dual-panel diagnostics chart for travel-direction lock stabilization.

    Plots unwrapped heading trajectories (raw, travel trend, and locked viewer heading)
    with deadband envelope in top panel, and pan angular velocities with drift in bottom panel.

    Args:
        times: Sequence of frame timestamps in seconds.
        raw_yaw: Raw camera heading angles in degrees.
        trend_yaw: Estimated forward travel direction trend in degrees.
        locked_yaw: Final stabilized viewer heading angles in degrees.
        deadband_deg: Angular deadband threshold in degrees.
        damping: Damping factor used in steering.
        mode: Steering mode string.
        output_png: Target destination path for PNG chart.
        fps: Video playback framerate.

    Returns:
        True if graph was successfully created and saved, False otherwise.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
        import numpy as np
    except ImportError:
        print("Warning: matplotlib/numpy not installed. Graph skipped.")
        return False

    n = len(times)
    if n == 0:
        print("Warning: Empty times array for traveldir graph.")
        return False

    times_arr = np.array(times, dtype=np.float64)
    raw_arr = np.array(raw_yaw, dtype=np.float64)
    trend_arr = np.array(trend_yaw, dtype=np.float64)
    locked_arr = np.array(locked_yaw, dtype=np.float64)

    fig, axes = plt.subplots(2, 1, figsize=(11, 7.2), sharex=True, facecolor="#1a1a2e")
    for ax in axes:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#cccccc")
        for spine in ax.spines.values():
            spine.set_color("#444444")

    axes[0].set_title("Travel-Direction Lock Stabilization: Heading Trajectory & Steering Tracking", color="#ffffff", fontsize=12, fontweight="bold", pad=28)

    secax = axes[0].secondary_xaxis('top', functions=(lambda t: t * fps, lambda f: f / fps if fps else f))
    secax.set_xlabel(f"Frame Number (at {fps:.2f} fps)", color="#38bdf8", fontsize=9, fontweight="bold", labelpad=8)
    secax.tick_params(colors="#38bdf8")
    secax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=20, steps=[1, 2, 2.5, 5, 10]))

    axes[0].plot(times_arr, raw_arr, color="#ff1744", linewidth=1.3, linestyle="--", label="Raw Heading (Yaw)", alpha=0.85)
    axes[0].plot(times_arr, trend_arr, color="#ffd600", linewidth=1.5, linestyle="-.", label="Forward Travel Trend", alpha=0.9)
    axes[0].plot(times_arr, locked_arr, color="#00e676", linewidth=2.0, linestyle="-", label="Stabilized View Heading", alpha=1.0)

    if deadband_deg > 0:
        axes[0].fill_between(
            times_arr,
            trend_arr - deadband_deg,
            trend_arr + deadband_deg,
            color="#00e5ff",
            alpha=0.15,
            label=f"Deadband Envelope (±{deadband_deg:.1f}°)"
        )

    steer_err = np.abs(locked_arr - raw_arr)
    if len(steer_err) > 0 and np.max(steer_err) > 0.05:
        idx_max = int(np.argmax(steer_err))
        val_max = (locked_arr - raw_arr)[idx_max]
        t_max = times_arr[idx_max]
        f_max = int(round(t_max * fps))
        axes[0].scatter([t_max], [locked_arr[idx_max]], color="#ffea00", s=35, zorder=5)
        offset_pts = 16 if val_max >= 0 else -20
        va = "bottom" if val_max >= 0 else "top"
        lbl_peak = f"Frame {f_max}: Steer {val_max:+.1f}°"
        axes[0].annotate(
            lbl_peak,
            xy=(t_max, locked_arr[idx_max]),
            xytext=(0, offset_pts),
            textcoords="offset points",
            ha="center",
            va=va,
            fontsize=8,
            color="#ffea00",
            fontweight="bold",
            arrowprops=dict(arrowstyle="->", color="#ffea00", lw=0.9, shrinkA=2, shrinkB=2)
        )

    axes[0].axhline(0, color="#ffffff", linewidth=0.6, linestyle=":", alpha=0.4)
    axes[0].set_ylabel("Heading / Yaw (deg)", color="#cccccc")
    axes[0].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper left", bbox_to_anchor=(1.02, 1.0))
    axes[0].grid(True, alpha=0.15, color="#888")

    if len(times_arr) > 1:
        raw_vel = np.abs(np.gradient(raw_arr, times_arr))
        locked_vel = np.abs(np.gradient(locked_arr, times_arr))
    else:
        raw_vel = np.zeros_like(raw_arr)
        locked_vel = np.zeros_like(locked_arr)

    heading_error = np.abs(trend_arr - raw_arr)

    axes[1].plot(times_arr, raw_vel, color="#ff5252", linewidth=1.1, linestyle="--", label="Raw Pan Rate (°/s)", alpha=0.7)
    axes[1].plot(times_arr, locked_vel, color="#00e676", linewidth=1.6, linestyle="-", label="Stabilized Pan Rate (°/s)", alpha=0.95)
    axes[1].plot(times_arr, heading_error, color="#00e5ff", linewidth=1.2, linestyle=":", label="Heading Deviation (°)", alpha=0.75)

    mean_raw_vel = float(np.mean(raw_vel)) if len(raw_vel) > 0 else 0.0
    mean_locked_vel = float(np.mean(locked_vel)) if len(locked_vel) > 0 else 0.0
    jitter_red_pct = max(0.0, (1.0 - (mean_locked_vel / max(mean_raw_vel, 1e-4))) * 100.0) if mean_raw_vel > 0.05 else 0.0
    max_sway = float(np.max(heading_error)) if len(heading_error) > 0 else 0.0
    in_deadband_pct = float(np.mean(heading_error <= deadband_deg) * 100.0) if len(heading_error) > 0 else 100.0

    badge_text = (
        f"Mode: {mode}\n"
        f"Damping: {damping:.2f} | Deadband: ±{deadband_deg:.1f}°\n"
        f"Max Yaw Sway: {max_sway:.1f}°\n"
        f"In-Deadband: {in_deadband_pct:.1f}%\n"
        f"Pan Jitter Reduction: {jitter_red_pct:.1f}%"
    )
    axes[1].text(
        0.98, 0.92, badge_text,
        transform=axes[1].transAxes,
        ha="right", va="top",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#0f3460", edgecolor="#38bdf8", alpha=0.9),
        fontsize=8, color="#ffffff", family="monospace"
    )

    axes[1].axhline(0, color="#ffffff", linewidth=0.6, linestyle=":", alpha=0.4)
    axes[1].set_ylabel("Pan Rate (°/s) / Drift (°)", color="#cccccc")
    axes[1].set_xlabel("Time (seconds)", color="#cccccc", labelpad=8)
    axes[1].xaxis.set_major_locator(ticker.MaxNLocator(nbins=20, steps=[1, 2, 2.5, 5, 10]))
    axes[1].xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, pos: f"{x:g}"))
    axes[1].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper left", bbox_to_anchor=(1.02, 1.0))
    axes[1].grid(True, alpha=0.15, color="#888")

    plt.tight_layout()
    plt.savefig(output_png, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"[OK] Travel-direction diagnostic graph saved: {output_png}")
    return True


def main():
    """CLI entry point for stabilization trajectory visualization and overlay generation."""
    parser = argparse.ArgumentParser(
        description="Visualize telemetry and optical stabilization corrections."
    )
    parser.add_argument("sendcmd_l", nargs="?", default="", help="Path to left or equirect sendcmd file")
    parser.add_argument("sendcmd_r", nargs="?", default="", help="Path to right sendcmd file (optional)")
    parser.add_argument("--trf", default="", help="Path to vidstab/kabsch/kopf transform or sendcmd file")
    parser.add_argument("--meta", default="", help="Path to traveldir JSON metadata file")
    parser.add_argument("--graph-type", default="auto", choices=["auto", "telemetry", "vidstab", "optical", "kabsch", "kopf", "cinematic", "traveldir"], help="Model type for graph target")
    parser.add_argument("--fps", type=float, default=29.97, help="Video FPS (default 29.97)")
    parser.add_argument("--output", default="", help="Output base filename/path")
    parser.add_argument("--no-graph", action="store_true", help="Skip PNG graph generation")
    parser.add_argument("--no-ass", action="store_true", help="Skip ASS overlay generation")
    parser.add_argument("--fusion-method", default="none", choices=["none", "ekf", "mahony", "complementary", "disabled"], help="IMU sensor fusion filter used")
    parser.add_argument("--fusion-gain", type=float, default=0.5, help="IMU sensor fusion gain")

    args = parser.parse_args()

    out_base = args.output
    if not out_base:
        if args.sendcmd_l:
            out_base = re.sub(r"_sendcmd_.*$", "", os.path.splitext(args.sendcmd_l)[0], flags=re.IGNORECASE)
        elif args.trf:
            out_base = os.path.splitext(args.trf)[0]
        else:
            print("Error: Specify a sendcmd file or --trf file")
            sys.exit(1)
    
    out_base = re.sub(r"(_sendcmd_.*|_sendcmd|_kopf|_kabsch|_telemetry|_optical|_cinematic|_traveldir)$", "", out_base, flags=re.IGNORECASE)

    # Determine effective graph type
    g_type = args.graph_type
    if g_type == "auto":
        if args.sendcmd_l and not args.trf:
            g_type = "telemetry"
        else:
            g_type = "optical"

    if args.sendcmd_l and os.path.exists(args.sendcmd_l) and g_type == "telemetry":
        print(f"Parsing telemetry: {args.sendcmd_l}")
        times_l, by_time_l = group_by_frame(parse_sendcmd(args.sendcmd_l))
        print_stats("Left", times_l, by_time_l)

        times_r, by_time_r = [], {}
        if args.sendcmd_r and os.path.exists(args.sendcmd_r):
            print(f"Parsing telemetry right: {args.sendcmd_r}")
            times_r, by_time_r = group_by_frame(parse_sendcmd(args.sendcmd_r))
            print_stats("Right", times_r, by_time_r)

        if not args.no_graph:
            generate_graph(
                times_l, by_time_l, times_r, by_time_r, out_base + "_telemetry_graph.png",
                fps=args.fps, fusion_method=getattr(args, "fusion_method", "none"),
                fusion_gain=getattr(args, "fusion_gain", 0.5)
            )

        if not args.no_ass:
            generate_ass(times_l, by_time_l, out_base + "_overlay_l.ass", fps=args.fps, label="Left")
            if times_r:
                generate_ass(times_r, by_time_r, out_base + "_overlay_r.ass", fps=args.fps, label="Right")

    elif g_type == "traveldir" and not args.no_graph:
        meta_file = args.meta
        if not meta_file:
            candidates = [
                f"{out_base}_traveldir_meta.json",
                f"{out_base}_meta.json",
            ]
            trf_cand = args.trf if (args.trf and os.path.exists(args.trf)) else (args.sendcmd_l if (args.sendcmd_l and os.path.exists(args.sendcmd_l)) else "")
            if trf_cand:
                candidates.append(trf_cand.replace("_sendcmd_traveldir.txt", "_traveldir_meta.json"))
                candidates.append(trf_cand.replace(".txt", "_meta.json"))
            for cand in candidates:
                if cand and cand.endswith(".json") and os.path.exists(cand):
                    meta_file = cand
                    break

        graph_rendered = False
        if meta_file and os.path.exists(meta_file):
            print(f"Parsing traveldir metadata: {meta_file}")
            try:
                with open(meta_file, "r", encoding="utf-8") as f_meta:
                    m_data = json.load(f_meta)
                times = m_data.get("times", [])
                raw_yaw = m_data.get("raw_yaw", [])
                trend_yaw = m_data.get("trend_yaw", [])
                locked_yaw = m_data.get("locked_yaw", [])
                db_val = float(m_data.get("deadband_deg", 1.5))
                damp_val = float(m_data.get("damping", 0.90))
                mode_val = str(m_data.get("mode", "travel_direction"))
                fps_val = float(m_data.get("fps", args.fps))
                graph_rendered = generate_traveldir_graph(
                    times, raw_yaw, trend_yaw, locked_yaw,
                    deadband_deg=db_val, damping=damp_val, mode=mode_val,
                    output_png=out_base + "_traveldir_graph.png", fps=fps_val
                )
            except Exception as e_m:
                print(f"Warning: Failed loading traveldir metadata {meta_file}: {e_m}")

        if not graph_rendered:
            trf_file = args.trf if (args.trf and os.path.exists(args.trf)) else (args.sendcmd_l if (args.sendcmd_l and os.path.exists(args.sendcmd_l)) else "")
            if trf_file:
                print(f"Parsing traveldir transform file fallback: {trf_file}")
                frames, dx, dy, da = parse_trf(trf_file)
                if frames:
                    import numpy as np
                    t_recon = [f / args.fps for f in frames]
                    steer_arr = np.array(dx)
                    raw_y = -np.cumsum(steer_arr)
                    trend_y = np.zeros_like(raw_y)
                    locked_y = raw_y + steer_arr
                    generate_traveldir_graph(
                        t_recon, raw_y, trend_y, locked_y,
                        deadband_deg=1.5, damping=0.90, mode="travel_direction",
                        output_png=out_base + "_traveldir_graph.png", fps=args.fps
                    )

    else:
        trf_file = args.trf if (args.trf and os.path.exists(args.trf)) else (args.sendcmd_l if (args.sendcmd_l and os.path.exists(args.sendcmd_l)) else "")
        if trf_file:
            print(f"Parsing {g_type} transform file: {trf_file}")
            frames, dx, dy, da = parse_trf(trf_file)
            if frames and not args.no_graph:
                if g_type == "kopf":
                    generate_optical_graph(frames, dx, dy, da, out_base + "_kopf_graph.png", fps=args.fps, model_name="Kopf 3D-2D Vision Keyframe")
                elif g_type == "kabsch":
                    generate_optical_graph(frames, dx, dy, da, out_base + "_kabsch_graph.png", fps=args.fps, model_name="Kabsch SVD 360 Rotation")
                elif g_type == "cinematic":
                    generate_optical_graph(frames, dx, dy, da, out_base + "_cinematic_graph.png", fps=args.fps, model_name="Cinematic SO(3) Path Optimization")
                else:
                    generate_optical_graph(frames, dx, dy, da, out_base + "_vidstab_graph.png", fps=args.fps, model_name="VidSTAB 2D Motion")

    print()
    print("-- Visualization Complete --")


if __name__ == "__main__":
    main()
