#!/usr/bin/env python3
"""
Privileged fan control helper script.
This script runs with sudo to read/write fan control sysfs files.

Usage:
    fan_helper.py get_status
    fan_helper.py set_mode <pwm_num> <mode>
    fan_helper.py set_pwm_mode <pwm_num> <pwm_mode>
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
        print(f"Error writing to {path}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return False

def read_temp_sensor(sensor_path):
    """Read temperature from any hwmon sensor (returns °C)."""
    try:
        with open(sensor_path) as f:
            return int(f.read().strip()) / 1000.0
    except Exception as e:
        print(f"Error reading temp sensor {sensor_path}: {e}", file=sys.stderr, flush=True)
        return None

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
            "pwm_mode": 1,  # 0=DC, 1=PWM
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
        
        # PWM Mode (0=DC, 1=PWM)
        pwm_mode = read_file(os.path.join(hwmon, f"pwm{i}_mode"))
        if pwm_mode:
            try:
                fan_data["pwm_mode"] = int(pwm_mode)
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
    """Set fan mode.
    
    Modes:
        0: Off (fan stopped)
        1: Manual PWM (fixed PWM percentage)
        2: Manual Curve (temperature-based automatic control)
        3: Target RPM (maintain target speed)
        5: BIOS Control (motherboard firmware control)
    
    Note: Mode 4 (Smart Fan IV) is not available on this chipset.
    """
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    mode = int(mode)
    if mode not in [0, 1, 2, 3, 5]:
        return {"error": f"Invalid mode: {mode}. Must be 0, 1, 2, 3, or 5 (mode 4 not supported)"}
    
    path = os.path.join(hwmon, f"pwm{pwm_num}_enable")
    if write_file(path, mode):
        return {"success": True, "pwm": pwm_num, "mode": mode}
    return {"error": f"Failed to set mode for pwm{pwm_num}"}

def set_pwm_mode(pwm_num, pwm_mode):
    """Set PWM mode (0=DC, 1=PWM)."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    pwm_mode = int(pwm_mode)
    if pwm_mode not in [0, 1]:
        return {"error": f"Invalid PWM mode: {pwm_mode}. Use 0 for DC or 1 for PWM"}
    
    path = os.path.join(hwmon, f"pwm{pwm_num}_mode")
    print(f"[DEBUG] Setting PWM mode for pwm{pwm_num}: mode={pwm_mode}, path={path}", file=sys.stderr, flush=True)
    
    # Check if file exists
    if not os.path.exists(path):
        print(f"[DEBUG] PWM mode file does not exist: {path}", file=sys.stderr, flush=True)
        return {"error": f"PWM mode file not found: {path}"}
    
    # Try to read current value
    current = read_file(path)
    print(f"[DEBUG] Current PWM mode value: {current}", file=sys.stderr, flush=True)
    
    if write_file(path, pwm_mode):
        # Verify the write
        new_value = read_file(path)
        print(f"[DEBUG] PWM mode set successfully. New value: {new_value}", file=sys.stderr, flush=True)
        return {"success": True, "pwm": pwm_num, "pwm_mode": pwm_mode}
    
    print(f"[DEBUG] Failed to write PWM mode to {path}", file=sys.stderr, flush=True)
    return {"error": f"Failed to set PWM mode for pwm{pwm_num}"}


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

def set_target_rpm(fan_num, rpm):
    """Set target RPM for fan (mode 3 - Fan Speed Cruise)."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    rpm = max(0, int(rpm))
    path = os.path.join(hwmon, f"fan{fan_num}_target")
    
    print(f"[FAN-CONTROL] Setting fan {fan_num} target RPM to {rpm}", file=sys.stderr, flush=True)
    
    if not os.path.exists(path):
        print(f"[FAN-CONTROL] Target RPM file does not exist: {path}", file=sys.stderr, flush=True)
        return {"error": f"Target RPM file not found: {path}"}
    
    if write_file(path, rpm):
        print(f"[FAN-CONTROL] ✓ Fan {fan_num} target RPM set to {rpm}", file=sys.stderr, flush=True)
        return {"success": True, "fan": fan_num, "target_rpm": rpm}
    
    print(f"[FAN-CONTROL] ✗ Failed to set target RPM for fan {fan_num}", file=sys.stderr, flush=True)
    return {"error": f"Failed to set target RPM for fan{fan_num}"}


def set_temp_source(pwm_num, temp_source):
    """Set temperature source for a fan (1-13)."""
    hwmon = find_hwmon()
    if not hwmon:
        return {"error": "nct6779 not found"}
    
    temp_source = int(temp_source)
    if temp_source < 1 or temp_source > 12:
        return {"error": f"Invalid temp source: {temp_source}. Must be 1-12"}
    
    # Check current mode - temp source can only be set in modes 1, 2, 3
    mode_path = os.path.join(hwmon, f"pwm{pwm_num}_enable")
    current_mode = read_file(mode_path)
    if current_mode and int(current_mode) not in [1, 2, 3]:
        return {"error": f"Cannot set temp source in mode {current_mode}. Fan must be in mode 1, 2, or 3"}
    
    path = os.path.join(hwmon, f"pwm{pwm_num}_temp_sel")
    
    print(f"[FAN-CONTROL] Setting fan {pwm_num} temp source to {temp_source}", file=sys.stderr, flush=True)
    
    if not os.path.exists(path):
        print(f"[FAN-CONTROL] Temp source file does not exist: {path}", file=sys.stderr, flush=True)
        return {"error": f"Temp source file not found: {path}"}
    
    if write_file(path, temp_source):
        print(f"[FAN-CONTROL] ✓ Fan {pwm_num} temp source set to {temp_source}", file=sys.stderr, flush=True)
        return {"success": True, "pwm": pwm_num, "temp_source": temp_source}
    
    print(f"[FAN-CONTROL] ✗ Failed to set temp source for fan {pwm_num}", file=sys.stderr, flush=True)
    return {"error": f"Failed to set temp source for pwm{pwm_num}"}


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
            print(f"[FAN-CONTROL] Setting fan {sys.argv[2]} to mode {sys.argv[3]}", file=sys.stderr, flush=True)
            result = set_mode(int(sys.argv[2]), int(sys.argv[3]))
            if "success" in result:
                print(f"[FAN-CONTROL] ✓ Fan {sys.argv[2]} mode set to {sys.argv[3]}", file=sys.stderr, flush=True)
            else:
                print(f"[FAN-CONTROL] ✗ Failed to set fan {sys.argv[2]} mode: {result.get('error')}", file=sys.stderr, flush=True)
        elif cmd == "set_pwm_mode" and len(sys.argv) >= 4:
            mode_name = "DC" if sys.argv[3] == "0" else "PWM"
            print(f"[FAN-CONTROL] Setting fan {sys.argv[2]} PWM mode to {mode_name}", file=sys.stderr, flush=True)
            result = set_pwm_mode(int(sys.argv[2]), int(sys.argv[3]))
            if "success" in result:
                print(f"[FAN-CONTROL] ✓ Fan {sys.argv[2]} PWM mode set to {mode_name}", file=sys.stderr, flush=True)
            else:
                print(f"[FAN-CONTROL] ✗ Failed to set fan {sys.argv[2]} PWM mode: {result.get('error')}", file=sys.stderr, flush=True)
        elif cmd == "set_pwm" and len(sys.argv) >= 4:
            print(f"[FAN-CONTROL] Setting fan {sys.argv[2]} PWM to {sys.argv[3]}", file=sys.stderr, flush=True)
            result = set_pwm(int(sys.argv[2]), int(sys.argv[3]))
            if "success" in result:
                print(f"[FAN-CONTROL] ✓ Fan {sys.argv[2]} PWM set to {sys.argv[3]}", file=sys.stderr, flush=True)
            else:
                print(f"[FAN-CONTROL] ✗ Failed to set fan {sys.argv[2]} PWM: {result.get('error')}", file=sys.stderr, flush=True)
        elif cmd == "set_target_rpm" and len(sys.argv) >= 4:
            print(f"[FAN-CONTROL] Setting fan {sys.argv[2]} target RPM to {sys.argv[3]}", file=sys.stderr, flush=True)
            result = set_target_rpm(int(sys.argv[2]), int(sys.argv[3]))
            if "success" in result:
                print(f"[FAN-CONTROL] ✓ Fan {sys.argv[2]} target RPM set to {sys.argv[3]}", file=sys.stderr, flush=True)
            else:
                print(f"[FAN-CONTROL] ✗ Failed to set fan {sys.argv[2]} target RPM: {result.get('error')}", file=sys.stderr, flush=True)
        elif cmd == "set_temp_source" and len(sys.argv) >= 4:
            print(f"[FAN-CONTROL] Setting fan {sys.argv[2]} temp source to {sys.argv[3]}", file=sys.stderr, flush=True)
            result = set_temp_source(int(sys.argv[2]), int(sys.argv[3]))
            if "success" in result:
                print(f"[FAN-CONTROL] ✓ Fan {sys.argv[2]} temp source set to {sys.argv[3]}", file=sys.stderr, flush=True)
            else:
                print(f"[FAN-CONTROL] ✗ Failed to set fan {sys.argv[2]} temp source: {result.get('error')}", file=sys.stderr, flush=True)
        elif cmd == "set_curve" and len(sys.argv) >= 6:
            print(f"[FAN-CONTROL] Setting fan {sys.argv[2]} curve point {sys.argv[3]}: {sys.argv[4]}°C → PWM {sys.argv[5]}", file=sys.stderr, flush=True)
            result = set_curve_point(
                int(sys.argv[2]),  # pwm_num
                int(sys.argv[3]),  # point
                float(sys.argv[4]),  # temp
                int(sys.argv[5])  # pwm
            )
            if "success" in result:
                print(f"[FAN-CONTROL] ✓ Fan {sys.argv[2]} curve point {sys.argv[3]} set", file=sys.stderr, flush=True)
            else:
                print(f"[FAN-CONTROL] ✗ Failed to set fan {sys.argv[2]} curve: {result.get('error')}", file=sys.stderr, flush=True)
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
