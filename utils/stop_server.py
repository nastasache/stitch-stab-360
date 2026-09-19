import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import psutil

def stop_stitchstab_server():
    current_pid = os.getpid()
    stopped = False
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == current_pid:
                continue
            if "python" in (p.info["name"] or "").lower():
                cmd = " ".join(p.info["cmdline"] or [])
                if "server:app" in cmd:
                    print(f"[INFO] Stopping server process PID {p.info['pid']}...")
                    p.kill()
                    stopped = True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return stopped

if __name__ == "__main__":
    if stop_stitchstab_server():
        print("[SUCCESS] StitchStab 360 server stopped cleanly.")
    else:
        print("[INFO] No running StitchStab 360 server process was found.")
