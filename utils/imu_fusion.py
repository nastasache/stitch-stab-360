import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""6-Axis IMU sensor fusion and attitude estimation (Mahony, Complementary, EKF).

Fuses tri-axial gyroscope angular rates and accelerometer gravity vectors to
estimate drift-compensated continuous Euler angles (roll, pitch, yaw) and
quaternion orientations.
"""

import math
import numpy as np

class MahonyFilter6Axis:
    """Mahony filter for 6-axis IMU (Gyroscope + Accelerometer).

    Corrects gyroscope orientation drift by fusing the accelerometer's gravity
    vector via proportional-integral (PI) feedback.
    """
    def __init__(self, sample_rate: float = 30.0, kp: float = 0.5, ki: float = 0.0):
        """Initialize Mahony 6-axis filter parameters.

        Args:
            sample_rate: Sampling frequency in Hz.
            kp: Proportional feedback gain.
            ki: Integral feedback gain.
        """
        self.sample_rate = sample_rate
        self.dt = 1.0 / max(sample_rate, 1e-4)
        self.kp = float(kp)
        self.ki = float(ki)
        # Quaternion [w, x, y, z]
        self.q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        self.integral_fb = np.array([0.0, 0.0, 0.0], dtype=np.float64)

    def reset(self):
        """Reset filter quaternion to identity and clear integral error feedback."""
        self.q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)
        self.integral_fb = np.array([0.0, 0.0, 0.0], dtype=np.float64)

    def update(self, gyro: np.ndarray, accel: np.ndarray) -> np.ndarray:
        """Update orientation quaternion from gyro rate and accelerometer vector.

        Args:
            gyro: Tri-axial angular velocity array [gx, gy, gz] in radians/sec.
            accel: Tri-axial acceleration vector [ax, ay, az] in m/s^2 or g.

        Returns:
            np.ndarray: Updated unit quaternion [w, x, y, z].
        """
        gx, gy, gz = float(gyro[0]), float(gyro[1]), float(gyro[2])
        ax, ay, az = float(accel[0]), float(accel[1]), float(accel[2])

        q0, q1, q2, q3 = self.q

        acc_norm = math.sqrt(ax * ax + ay * ay + az * az)
        if acc_norm > 1e-4:
            ax /= acc_norm
            ay /= acc_norm
            az /= acc_norm

            # Estimated direction of gravity from current quaternion
            vx = 2.0 * (q1 * q3 - q0 * q2)
            vy = 2.0 * (q0 * q1 + q2 * q3)
            vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3

            # Error is cross product between estimated and measured direction of gravity
            ex = (ay * vz - az * vy)
            ey = (az * vx - ax * vz)
            ez = (ax * vy - ay * vx)

            if self.ki > 0.0:
                self.integral_fb[0] += ex * self.ki * self.dt
                self.integral_fb[1] += ey * self.ki * self.dt
                self.integral_fb[2] += ez * self.ki * self.dt
                gx += self.integral_fb[0]
                gy += self.integral_fb[1]
                gz += self.integral_fb[2]

            gx += self.kp * ex
            gy += self.kp * ey
            gz += self.kp * ez

        # Integrate rate of change of quaternion
        pa = q1
        pb = q2
        pc = q3
        q0_dot = 0.5 * (-pa * gx - pb * gy - pc * gz)
        q1_dot = 0.5 * (q0 * gx + pb * gz - pc * gy)
        q2_dot = 0.5 * (q0 * gy - pa * gz + pc * gx)
        q3_dot = 0.5 * (q0 * gz + pa * gy - pb * gx)

        q0 += q0_dot * self.dt
        q1 += q1_dot * self.dt
        q2 += q2_dot * self.dt
        q3 += q3_dot * self.dt

        # Normalize quaternion
        q_norm = math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
        if q_norm > 1e-6:
            self.q = np.array([q0 / q_norm, q1 / q_norm, q2 / q_norm, q3 / q_norm], dtype=np.float64)

        return self.q


class ComplementaryFilter6Axis:
    """6-Axis Complementary Filter for pitch and roll gravity fusion.

    Fuses high-frequency gyroscope integration with low-frequency accelerometer
    tilt angles. Yaw is derived via pure gyro integration since accelerometer
    cannot measure vertical rotation.
    """
    def __init__(self, sample_rate: float = 30.0, alpha: float = 0.98):
        """Initialize Complementary filter.

        Args:
            sample_rate: Sampling frequency in Hz.
            alpha: Gyroscope weighting coefficient in [0.0, 1.0].
        """
        self.sample_rate = sample_rate
        self.dt = 1.0 / max(sample_rate, 1e-4)
        self.alpha = float(alpha)
        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0

    def update(self, gyro_deg: np.ndarray, accel: np.ndarray) -> tuple[float, float, float]:
        """Update attitude angles from angular rate and acceleration.

        Args:
            gyro_deg: Tri-axial angular velocity [gx, gy, gz] in deg/sec.
            accel: Tri-axial acceleration [ax, ay, az] in m/s^2 or g.

        Returns:
            tuple[float, float, float]: Fused attitude angles (roll, pitch, yaw) in degrees.
        """
        gx, gy, gz = float(gyro_deg[0]), float(gyro_deg[1]), float(gyro_deg[2])
        ax, ay, az = float(accel[0]), float(accel[1]), float(accel[2])

        # Accel angles (pitch & roll)
        acc_roll = math.degrees(math.atan2(ay, az + 1e-8))
        acc_pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az) + 1e-8))

        # Integrate gyro
        self.roll = self.alpha * (self.roll + gx * self.dt) + (1.0 - self.alpha) * acc_roll
        self.pitch = self.alpha * (self.pitch + gy * self.dt) + (1.0 - self.alpha) * acc_pitch
        self.yaw += gz * self.dt

        return self.roll, self.pitch, self.yaw


class EKF6Axis:
    """Simplified Extended Kalman Filter for 6-axis attitude estimation.

    Tracks roll, pitch, and gyroscope bias state vectors with covariance updates.
    """
    def __init__(self, sample_rate: float = 30.0, q_angle: float = 0.001, q_bias: float = 0.003, r_measure: float = 0.03):
        """Initialize EKF process and measurement covariance parameters.

        Args:
            sample_rate: Sampling frequency in Hz.
            q_angle: Process noise variance for angle estimation.
            q_bias: Process noise variance for gyro bias estimation.
            r_measure: Measurement noise variance for accelerometer tilt.
        """
        self.dt = 1.0 / max(sample_rate, 1e-4)
        self.q_angle = q_angle
        self.q_bias = q_bias
        self.r_measure = r_measure

        self.roll = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self.bias_r = 0.0
        self.bias_p = 0.0

        self.p_roll = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float64)
        self.p_pitch = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float64)

    def _kalman_step(self, angle: float, bias: float, p: np.ndarray, rate: float, measure_angle: float) -> tuple[float, float, np.ndarray]:
        """Execute single 1D Kalman predict and update step.

        Args:
            angle: Prior angle estimate in degrees.
            bias: Prior gyro bias estimate in degrees/sec.
            p: 2x2 state covariance matrix.
            rate: Measured angular rate in degrees/sec.
            measure_angle: Measurement angle from accelerometer in degrees.

        Returns:
            tuple[float, float, np.ndarray]: Posterior (angle, bias, p) estimates.
        """
        # Predict
        rate_unbiased = rate - bias
        angle += self.dt * rate_unbiased

        p[0, 0] += self.dt * (self.dt * p[1, 1] - p[0, 1] - p[1, 0] + self.q_angle)
        p[0, 1] -= self.dt * p[1, 1]
        p[1, 0] -= self.dt * p[1, 1]
        p[1, 1] += self.q_bias * self.dt

        # Update
        s = p[0, 0] + self.r_measure
        k0 = p[0, 0] / s
        k1 = p[1, 0] / s

        y = measure_angle - angle
        angle += k0 * y
        bias += k1 * y

        p00_temp = p[0, 0]
        p01_temp = p[0, 1]
        p[0, 0] -= k0 * p00_temp
        p[0, 1] -= k0 * p01_temp
        p[1, 0] -= k1 * p00_temp
        p[1, 1] -= k1 * p01_temp

        return angle, bias, p

    def update(self, gyro_deg: np.ndarray, accel: np.ndarray) -> tuple[float, float, float]:
        """Update attitude angles using Extended Kalman Filter.

        Args:
            gyro_deg: Tri-axial angular velocity [gx, gy, gz] in deg/sec.
            accel: Tri-axial acceleration [ax, ay, az] in m/s^2 or g.

        Returns:
            tuple[float, float, float]: Filtered attitude angles (roll, pitch, yaw) in degrees.
        """
        gx, gy, gz = float(gyro_deg[0]), float(gyro_deg[1]), float(gyro_deg[2])
        ax, ay, az = float(accel[0]), float(accel[1]), float(accel[2])

        acc_roll = math.degrees(math.atan2(ay, az + 1e-8))
        acc_pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az) + 1e-8))

        self.roll, self.bias_r, self.p_roll = self._kalman_step(self.roll, self.bias_r, self.p_roll, gx, acc_roll)
        self.pitch, self.bias_p, self.p_pitch = self._kalman_step(self.pitch, self.bias_p, self.p_pitch, gy, acc_pitch)
        self.yaw += gz * self.dt

        return self.roll, self.pitch, self.yaw


def quaternion_to_euler(q: np.ndarray) -> tuple[float, float, float]:
    """Convert unit quaternion [w, x, y, z] to Euler angles (roll, pitch, yaw) in degrees.

    Args:
        q: 4-element array-like quaternion [w, x, y, z].

    Returns:
        tuple[float, float, float]: Euler angles (roll, pitch, yaw) in degrees.
    """
    w, x, y, z = float(q[0]), float(q[1]), float(q[2]), float(q[3])

    # Roll (x-axis)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    # Pitch (y-axis)
    sinp = 2.0 * (w * y - z * x)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(90.0, sinp)
    else:
        pitch = math.degrees(math.asin(sinp))

    # Yaw (z-axis)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))

    return roll, pitch, yaw


def fuse_6axis_sequence(
    gyro_data: np.ndarray,
    accel_data: np.ndarray,
    fps: float = 30.0,
    method: str = "ekf",
    gain: float = 0.51
) -> np.ndarray:
    """Apply 6-axis sensor fusion over a continuous sample sequence.

    Args:
        gyro_data: (N, 3) array of tri-axial angular rates in deg/sec.
        accel_data: (N, 3) array of tri-axial acceleration vectors in m/s^2 or g.
        fps: Sampling frame rate in Hz.
        method: Fusion algorithm ('ekf', 'mahony', 'complementary', or 'gyro_only').
        gain: Filter tuning parameter (kp for Mahony, alpha for complementary, scale for EKF).

    Returns:
        np.ndarray: (N, 3) array of fused Euler angles [roll, pitch, yaw] in degrees.
    """
    n_samples = len(gyro_data)
    fused = np.zeros((n_samples, 3), dtype=np.float64)
    method = method.lower()

    if method == "mahony":
        flt = MahonyFilter6Axis(sample_rate=fps, kp=gain)
        for i in range(n_samples):
            # Gyro converted to rad/s for Mahony
            gyro_rad = np.radians(gyro_data[i])
            q = flt.update(gyro_rad, accel_data[i])
            fused[i] = quaternion_to_euler(q)
    elif method == "complementary":
        alpha_clamped = min(max(float(gain), 0.001), 0.999)
        flt = ComplementaryFilter6Axis(sample_rate=fps, alpha=alpha_clamped)
        for i in range(n_samples):
            fused[i] = flt.update(gyro_data[i], accel_data[i])
    elif method == "ekf":
        r_meas = max(1e-4, float(gain) * (0.03 / 0.51))
        flt = EKF6Axis(sample_rate=fps, r_measure=r_meas)
        for i in range(n_samples):
            fused[i] = flt.update(gyro_data[i], accel_data[i])
    else:
        # Pass-through gyro integration
        dt = 1.0 / max(fps, 1e-4)
        curr = np.zeros(3, dtype=np.float64)
        for i in range(n_samples):
            curr += gyro_data[i] * dt
            fused[i] = curr

    return fused


if __name__ == "__main__":
    if "--test-synthetic" in sys.argv:
        print("[utils/imu_fusion.py] Running synthetic validation...")
        fps = 30.0
        n_frames = 90
        gyro = np.zeros((n_frames, 3))
        gyro[:, 0] = 5.0 # 5 deg/s roll drift
        accel = np.zeros((n_frames, 3))
        accel[:, 2] = 1.0 # upright gravity

        fused_mahony = fuse_6axis_sequence(gyro, accel, fps=fps, method="mahony", gain=0.5)
        fused_comp   = fuse_6axis_sequence(gyro, accel, fps=fps, method="complementary", gain=0.95)
        fused_ekf    = fuse_6axis_sequence(gyro, accel, fps=fps, method="ekf")

        print(f"Final Roll (Uncompensated): {5.0 * (n_frames / fps):.2f}°")
        print(f"Final Roll (Mahony):        {fused_mahony[-1, 0]:.2f}° (Gravity corrected)")
        print(f"Final Roll (Complementary): {fused_comp[-1, 0]:.2f}° (Gravity corrected)")
        print(f"Final Roll (EKF):           {fused_ekf[-1, 0]:.2f}° (Gravity corrected)")
        assert abs(fused_mahony[-1, 0]) < 15.0, "Mahony failed to bound drift!"
        print("[utils/imu_fusion.py] Synthetic test PASSED.")
        sys.exit(0)
