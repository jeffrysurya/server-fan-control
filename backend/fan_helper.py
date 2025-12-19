#!/usr/bin/env python3
"""
Privileged fan control helper script.
This script runs with sudo to read/write fan control sysfs files.

Usage:
    fan_helper.py get_status
    fan_helper.py set_mode <pwm_num> <mode>
    fan_helper.py set_pwm <pwm_num> <value>
    fan_helper.py set_curve <pwm_num> <point> <temp> <pwm>
    fan_helper.py get_curve <pwm_num>
"""

import sys
import os
import json
import glob

HWMON_PATH = None

def find_hwmon():
    """Find the hwmon path for nct6779."""
    global HWMON_PATH
    if HWMON_PATH:
        return HWMON_PATH
    
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        name_file = os.path.join(hwmon, "name")
        if os.path.exists(name_file):
            with open(name_file) as f:
                if "nct6779" in f.read():
                    HWMON_PATH = hwmon
                    return HWMON_PATH
    return None

def read_file(path):
    """Read a sysfs file."""
    try:
        with open(path) as f:
            return f.read().strip()
    except (IOError, FileNotFoundError):
        return None

def write_file(path, value):
    """Write to a sysfs file."""
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except (IOError, PermissionError) as e:
        print(f"Error writing to {path}: {e}", file=sys.stderr)
        return False

def get_status():
    """Get current status of all fans."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    status = {
        "hwmon": hwmon,
        "fans": [],
        "temps": {}
    }
    
    # Read temperatures
    temp_map = {
        "SYSTIN": "temp1_input",
        "CPUTIN": "temp2_input",
        "AUXTIN0": "temp3_input",
        "AUXTIN1": "temp4_input",
        "AUXTIN2": "temp5_input",
    }
    
    for name, temp_file in temp_map.items():
        val = read_file(os.path.join(hwmon, temp_file))
        if val:
            try:
                status["temps"][name] = int(val) / 1000.0
            except ValueError:
                pass
    
    # Read k10temp (CPU actual temp)
    for k10 in glob.glob("/sys/class/hwmon/hwmon*"):
        name_file = os.path.join(k10, "name")
        if os.path.exists(name_file):
            with open(name_file) as f:
                if "k10temp" in f.read():
                    tctl = read_file(os.path.join(k10, "temp1_input"))
                    if tctl:
                        status["temps"]["CPU (Tctl)"] = int(tctl) / 1000.0
                    break
    
    # Read fan data
    for i in range(1, 6):
        fan_data = {
            "id": i,
            "name": f"Fan {i}",
            "rpm": 0,
            "pwm": 0,
            "pwm_percent": 0,
            "mode": 5,
            "curve": []
        }
        
        # RPM
        rpm = read_file(os.path.join(hwmon, f"fan{i}_input"))
        if rpm:
            try:
                fan_data["rpm"] = int(rpm)
            except ValueError:
                pass
        
        # PWM value
        pwm = read_file(os.path.join(hwmon, f"pwm{i}"))
        if pwm:
            try:
                fan_data["pwm"] = int(pwm)
                fan_data["pwm_percent"] = round(int(pwm) / 255 * 100, 1)
            except ValueError:
                pass
        
        # Mode
        mode = read_file(os.path.join(hwmon, f"pwm{i}_enable"))
        if mode:
            try:
                fan_data["mode"] = int(mode)
            except ValueError:
                pass
        
        # Curve points
        for p in range(1, 6):
            temp = read_file(os.path.join(hwmon, f"pwm{i}_auto_point{p}_temp"))
            pwm_val = read_file(os.path.join(hwmon, f"pwm{i}_auto_point{p}_pwm"))
            if temp and pwm_val:
                try:
                    fan_data["curve"].append({
                        "point": p,
                        "temp": int(temp) / 1000,
                        "pwm": int(pwm_val),
                        "pwm_percent": round(int(pwm_val) / 255 * 100, 1)
                    })
                except ValueError:
                    pass
        
        status["fans"].append(fan_data)
    
    return status

def set_mode(pwm_num, mode):
    """Set fan mode (1=manual, 5=auto/BIOS)."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    mode = int(mode)
    if mode not in [0, 1, 2, 5]:
        return {"error": f"Invalid mode: {mode}"}
    
    path = os.path.join(hwmon, f"pwm{pwm_num}_enable")
    if write_file(path, mode):
        return {"success": True, "pwm": pwm_num, "mode": mode}
    return {"error": f"Failed to set mode for pwm{pwm_num}"}

def set_pwm(pwm_num, value):
    """Set PWM value (0-255)."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    value = max(0, min(255, int(value)))
    path = os.path.join(hwmon, f"pwm{pwm_num}")
    if write_file(path, value):
        return {"success": True, "pwm": pwm_num, "value": value}
    return {"error": f"Failed to set PWM for pwm{pwm_num}"}

def set_curve_point(pwm_num, point, temp, pwm_val):
    """Set a curve point for a fan."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    point = int(point)
    if point < 1 or point > 5:
        return {"error": f"Invalid point: {point}"}
    
    temp_mc = int(float(temp) * 1000)  # Convert to millicelsius
    pwm_val = max(0, min(255, int(pwm_val)))
    
    temp_path = os.path.join(hwmon, f"pwm{pwm_num}_auto_point{point}_temp")
    pwm_path = os.path.join(hwmon, f"pwm{pwm_num}_auto_point{point}_pwm")
    
    result = {"success": True, "pwm": pwm_num, "point": point}
    
    if not write_file(temp_path, temp_mc):
        result["temp_error"] = True
    if not write_file(pwm_path, pwm_val):
        result["pwm_error"] = True
    
    if "temp_error" in result or "pwm_error" in result:
        result["success"] = False
    
    return result

def get_curve(pwm_num):
    """Get curve for a specific fan."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    curve = []
    for p in range(1, 6):
        temp = read_file(os.path.join(hwmon, f"pwm{pwm_num}_auto_point{p}_temp"))
        pwm_val = read_file(os.path.join(hwmon, f"pwm{pwm_num}_auto_point{p}_pwm"))
        if temp and pwm_val:
            curve.append({
                "point": p,
                "temp": int(temp) / 1000,
                "pwm": int(pwm_val),
                "pwm_percent": round(int(pwm_val) / 255 * 100, 1)
            })
    
    return {"pwm": pwm_num, "curve": curve}

def main():
    if len(sys.argv) < 2:
        print("Usage: fan_helper.py <command> [args...]", file=sys.stderr)
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    try:
        if cmd == "get_status":
            result = get_status()
        elif cmd == "set_mode" and len(sys.argv) >= 4:
            result = set_mode(int(sys.argv[2]), int(sys.argv[3]))
        elif cmd == "set_pwm" and len(sys.argv) >= 4:
            result = set_pwm(int(sys.argv[2]), int(sys.argv[3]))
        elif cmd == "set_curve" and len(sys.argv) >= 6:
            result = set_curve_point(
                int(sys.argv[2]),  # pwm_num
                int(sys.argv[3]),  # point
                float(sys.argv[4]),  # temp
                int(sys.argv[5])  # pwm
            )
        elif cmd == "get_curve" and len(sys.argv) >= 3:
            result = get_curve(int(sys.argv[2]))
        else:
            result = {"error": f"Unknown command or missing args: {cmd}"}
        
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
