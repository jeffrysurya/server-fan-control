#!/usr/bin/env python3
"""
Fan Control API Server
FastAPI backend with WebSocket for real-time fan monitoring and control.
"""

import asyncio
import json
import os
import glob
import subprocess
import logging
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path(__file__).parent
HELPER_SCRIPT = BASE_DIR / "fan_helper.py"
CONFIG_FILE = BASE_DIR / "config.json"
FRONTEND_DIR = BASE_DIR.parent / "frontend" / "dist"

# Default config
DEFAULT_CONFIG = {
    "fan_modes": {
        str(i): 5  # Default to mode 5 (BIOS control)
        for i in range(1, 6)
    },
    "curves": {
        str(i): [
            {"point": 1, "temp": 30, "pwm": 50},
            {"point": 2, "temp": 40, "pwm": 80},
            {"point": 3, "temp": 50, "pwm": 120},
            {"point": 4, "temp": 60, "pwm": 180},
            {"point": 5, "temp": 70, "pwm": 255},
        ]
        for i in range(1, 6)
    },
    "fan_names": {
        "1": "CPU Fan",
        "2": "Chassis Fan 1",
        "3": "Chassis Fan 2",
        "4": "Chassis Fan 3",
        "5": "Chassis Fan 4",
    },
    "pwm_modes": {
        str(i): 1  # 0=DC, 1=PWM (default to PWM)
        for i in range(1, 6)
    }
}

# Mode descriptions for API
MODE_DESCRIPTIONS = {
    0: "Off",
    1: "Manual PWM",
    2: "Manual Curve",
    3: "Target RPM",
    5: "BIOS Control"
}


def load_config() -> dict:
    """Load config from file or return defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config = json.load(f)
                
                # Migrate old config format (global mode) to new format (per-fan modes)
                if "mode" in config and "fan_modes" not in config:
                    logger.info("Migrating old config format to per-fan modes")
                    old_mode = config.pop("mode")
                    # Convert: "auto" -> mode 5, "manual" -> mode 1
                    new_mode = 5 if old_mode == "auto" else 1
                    config["fan_modes"] = {str(i): new_mode for i in range(1, 6)}
                    save_config(config)  # Save migrated config
                    logger.info(f"Migrated: '{old_mode}' -> mode {new_mode} for all fans")
                
                # Merge with defaults for any missing keys
                for key, value in DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                return config
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load config: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """Save config to file."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.info("Config saved")
    except IOError as e:
        logger.error(f"Failed to save config: {e}")


def run_helper(command: str, *args) -> dict:
    """Run the privileged helper script via sudo."""
    cmd = ["sudo", "-n", "python3", str(HELPER_SCRIPT), command] + [str(a) for a in args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            logger.error(f"Helper error: {result.stderr}")
            return {"error": result.stderr or "Command failed"}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out"}
    except json.JSONDecodeError:
        return {"error": "Invalid response from helper"}
    except Exception as e:
        return {"error": str(e)}


class SoftwareController:
    """Software-based fan control using external temperature sensors."""
    
    def __init__(self):
        self.running = False
        self.task = None
        self.last_pwm = {}  # Track last PWM for each fan
        self.last_temp = {}  # Track last temperature for each fan
        
    def calculate_pwm_from_curve(self, fan_id: int, temp: float, curve: list, 
                                  hysteresis_temp: float = 2.0,
                                  ramp_down_rate: int = 5) -> int:
        """Calculate PWM value from temperature using curve interpolation with hysteresis.
        
        Args:
            fan_id: Fan ID
            temp: Current temperature
            curve: Temperature curve points
            hysteresis_temp: Temperature hysteresis in °C (default 2°C)
            ramp_down_rate: Max PWM decrease per second when cooling (default 5)
        
        Returns:
            PWM value (0-255)
        """
        if not curve or len(curve) < 2:
            return 128  # Default to 50%
        
        # Sort curve by temperature
        sorted_curve = sorted(curve, key=lambda p: p["temp"])
        
        # Calculate target PWM from curve
        target_pwm = 128
        
        # Below minimum temp
        if temp <= sorted_curve[0]["temp"]:
            target_pwm = sorted_curve[0]["pwm"]
        # Above maximum temp
        elif temp >= sorted_curve[-1]["temp"]:
            target_pwm = sorted_curve[-1]["pwm"]
        else:
            # Interpolate between points
            for i in range(len(sorted_curve) - 1):
                if sorted_curve[i]["temp"] <= temp <= sorted_curve[i + 1]["temp"]:
                    # Linear interpolation
                    t1, p1 = sorted_curve[i]["temp"], sorted_curve[i]["pwm"]
                    t2, p2 = sorted_curve[i + 1]["temp"], sorted_curve[i + 1]["pwm"]
                    
                    ratio = (temp - t1) / (t2 - t1)
                    target_pwm = int(p1 + ratio * (p2 - p1))
                    break
        
        # Apply hysteresis
        last_pwm = self.last_pwm.get(fan_id, target_pwm)
        last_temp = self.last_temp.get(fan_id, temp)
        
        # Temperature is rising - aggressive response
        if temp > last_temp + 0.5:  # 0.5°C threshold to avoid noise
            # Immediately increase PWM
            final_pwm = target_pwm
        # Temperature is falling - slow response with hysteresis
        elif temp < last_temp - hysteresis_temp:
            # Only decrease PWM gradually
            if target_pwm < last_pwm:
                # Ramp down slowly
                final_pwm = max(target_pwm, last_pwm - ramp_down_rate)
            else:
                final_pwm = target_pwm
        # Temperature stable - maintain or adjust slowly
        else:
            # If target is higher, increase immediately
            if target_pwm > last_pwm:
                final_pwm = target_pwm
            # If target is lower, decrease slowly
            else:
                final_pwm = max(target_pwm, last_pwm - ramp_down_rate)
        
        # Store current values
        self.last_pwm[fan_id] = final_pwm
        self.last_temp[fan_id] = temp
        
        return max(0, min(255, final_pwm))
    
    def read_temperature(self, sensor_path: str) -> Optional[float]:
        """Read temperature from sensor path."""
        try:
            with open(sensor_path) as f:
                return int(f.read().strip()) / 1000.0
        except Exception as e:
            logger.error(f"Failed to read temp from {sensor_path}: {e}")
            return None
    
    async def control_loop(self, config: dict):
        """Main control loop for software-based fan control."""
        logger.info("Software controller started")
        self.running = True
        
        while self.running:
            try:
                software_control = config.get("software_control", {})
                
                for fan_id_str, settings in software_control.items():
                    if not settings.get("enabled", False):
                        continue
                    
                    fan_id = int(fan_id_str)
                    temp_source = settings.get("temp_source")
                    curve = settings.get("curve", [])
                    
                    if not temp_source or not curve:
                        continue
                    
                    # Read temperature
                    temp = self.read_temperature(temp_source)
                    if temp is None:
                        continue
                    
                    # Calculate PWM with hysteresis
                    pwm = self.calculate_pwm_from_curve(fan_id, temp, curve)
                    
                    # Set PWM
                    result = run_helper("set_pwm", fan_id, pwm)
                    if "error" in result:
                        logger.error(f"Failed to set PWM for fan {fan_id}: {result['error']}")
                
                await asyncio.sleep(1)  # Update every second
                
            except Exception as e:
                logger.error(f"Error in software control loop: {e}")
                await asyncio.sleep(5)
        
        logger.info("Software controller stopped")
    
    def start(self, config: dict):
        """Start the control loop."""
        if not self.running:
            self.task = asyncio.create_task(self.control_loop(config))
    
    def stop(self):
        """Stop the control loop."""
        self.running = False


# Global software controller
software_controller = SoftwareController()


class AutoTuner:
    """Automatic fan curve calibration and optimization."""
    
    def __init__(self):
        self.running = False
        self.progress = 0
        self.current_action = ""
        self.results = {}
        self.original_settings = {}
        
    async def calibrate_fan(self, fan_id: int) -> dict:
        """Calibrate a single fan's PWM-to-RPM curve."""
        logger.info(f"Calibrating fan {fan_id}")
        
        results = {
            "fan_id": fan_id,
            "min_pwm": 0,
            "pwm_rpm_map": {},
            "max_rpm": 0
        }
        
        # Save original mode
        status = run_helper("get_status")
        if "error" in status:
            return {"error": "Failed to get fan status"}
        
        # Set to manual mode
        run_helper("set_mode", fan_id, 1)
        await asyncio.sleep(1)
        
        # Test PWM levels
        test_pwms = [0, 50, 77, 102, 128, 153, 179, 204, 230, 255]
        
        for i, pwm in enumerate(test_pwms):
            if not self.running:
                return {"error": "Calibration cancelled"}
            
            self.current_action = f"Testing Fan {fan_id} at PWM {pwm} ({int((i+1)/len(test_pwms)*100)}%)"
            self.progress = int((i / len(test_pwms)) * 100)
            
            # Set PWM
            run_helper("set_pwm", fan_id, pwm)
            await asyncio.sleep(3)  # Wait for stabilization
            
            # Read RPM
            status = run_helper("get_status")
            if "error" not in status and status.get("fans"):
                fan_data = next((f for f in status["fans"] if f["id"] == fan_id), None)
                if fan_data:
                    rpm = fan_data.get("rpm", 0)
                    results["pwm_rpm_map"][pwm] = rpm
                    
                    if rpm > 0 and results["min_pwm"] == 0:
                        results["min_pwm"] = pwm
                    
                    if rpm > results["max_rpm"]:
                        results["max_rpm"] = rpm
                    
                    logger.info(f"Fan {fan_id} PWM {pwm} → {rpm} RPM")
        
        return results
    
    async def profile_temperature(self, temp_source: str, duration: int = 30) -> dict:
        """Profile system temperature characteristics."""
        logger.info(f"Profiling temperature from {temp_source}")
        
        temps = []
        
        for i in range(duration):
            if not self.running:
                return {"error": "Profiling cancelled"}
            
            self.current_action = f"Monitoring temperature ({i+1}/{duration}s)"
            self.progress = int((i / duration) * 100)
            
            try:
                with open(temp_source) as f:
                    temp = int(f.read().strip()) / 1000.0
                    temps.append(temp)
            except Exception as e:
                logger.error(f"Failed to read temperature: {e}")
            
            await asyncio.sleep(1)
        
        if not temps:
            return {"error": "No temperature data collected"}
        
        return {
            "min_temp": min(temps),
            "max_temp": max(temps),
            "avg_temp": sum(temps) / len(temps),
            "idle_temp": temps[0]
        }
    
    def generate_curve(self, fan_data: dict, temp_data: dict, profile: str = "balanced") -> list:
        """Generate optimal curve based on calibration and profile."""
        
        profiles = {
            "silent": {
                "temp_margin": 10,
                "min_pwm_percent": 0.3,
                "curve_aggression": 0.7
            },
            "balanced": {
                "temp_margin": 5,
                "min_pwm_percent": 0.4,
                "curve_aggression": 1.0
            },
            "performance": {
                "temp_margin": 0,
                "min_pwm_percent": 0.5,
                "curve_aggression": 1.3
            }
        }
        
        config = profiles.get(profile, profiles["balanced"])
        
        # Calculate temperature points
        idle = temp_data.get("idle_temp", 35)
        max_safe = 80  # Configurable
        
        temp_range = max_safe - idle
        min_pwm = max(fan_data.get("min_pwm", 77), 50)  # Ensure minimum 50
        
        # Generate 5-point curve
        curve = [
            {
                "point": 1,
                "temp": round(idle, 1),
                "pwm": max(int(min_pwm * config["min_pwm_percent"]), 50)
            },
            {
                "point": 2,
                "temp": round(idle + temp_range * 0.25, 1),
                "pwm": min(int(255 * 0.4 * config["curve_aggression"]), 255)
            },
            {
                "point": 3,
                "temp": round(idle + temp_range * 0.5, 1),
                "pwm": min(int(255 * 0.6 * config["curve_aggression"]), 255)
            },
            {
                "point": 4,
                "temp": round(idle + temp_range * 0.75, 1),
                "pwm": min(int(255 * 0.8 * config["curve_aggression"]), 255)
            },
            {
                "point": 5,
                "temp": max_safe,
                "pwm": 255
            }
        ]
        
        return curve
    
    async def run_auto_tune(self, fan_ids: list, temp_source: str, profile: str = "balanced"):
        """Run complete auto-tuning process."""
        self.running = True
        self.results = {
            "fan_calibration": {},
            "temp_profile": {},
            "generated_curves": {},
            "profile": profile,
            "timestamp": asyncio.get_event_loop().time()
        }
        
        try:
            # Phase 1: Temperature profiling
            self.current_action = "Profiling system temperature..."
            temp_profile = await self.profile_temperature(temp_source, duration=30)
            if "error" in temp_profile:
                self.results["error"] = temp_profile["error"]
                return self.results
            
            self.results["temp_profile"] = temp_profile
            
            # Phase 2: Fan calibration
            for i, fan_id in enumerate(fan_ids):
                if not self.running:
                    self.results["error"] = "Auto-tune cancelled"
                    return self.results
                
                self.current_action = f"Calibrating Fan {fan_id}..."
                self.progress = int((i / len(fan_ids)) * 100)
                
                fan_cal = await self.calibrate_fan(fan_id)
                if "error" in fan_cal:
                    logger.error(f"Failed to calibrate fan {fan_id}: {fan_cal['error']}")
                    continue
                
                self.results["fan_calibration"][str(fan_id)] = fan_cal
                
                # Generate curve
                curve = self.generate_curve(fan_cal, temp_profile, profile)
                self.results["generated_curves"][str(fan_id)] = curve
            
            self.current_action = "Auto-tune complete!"
            self.progress = 100
            
        except Exception as e:
            logger.error(f"Auto-tune error: {e}")
            self.results["error"] = str(e)
        
        finally:
            self.running = False
        
        return self.results
    
    def cancel(self):
        """Cancel auto-tuning."""
        self.running = False


# Global auto-tuner
auto_tuner = AutoTuner()


def apply_saved_settings():
    """Apply saved settings on startup."""
    config = load_config()
    
    # Apply PWM modes first
    pwm_modes = config.get("pwm_modes", {})
    for fan_id in range(1, 6):
        pwm_mode = pwm_modes.get(str(fan_id), 1)  # Default to PWM mode
        result = run_helper("set_pwm_mode", fan_id, pwm_mode)
        if "error" in result:
            logger.error(f"Failed to set PWM mode for fan {fan_id}: {result['error']}")
    
    # Apply per-fan modes
    fan_modes = config.get("fan_modes", {})
    logger.info("Applying saved per-fan mode settings...")
    
    for fan_id in range(1, 6):
        mode = fan_modes.get(str(fan_id), 5)  # Default to BIOS control
        result = run_helper("set_mode", fan_id, mode)
        
        if "error" in result:
            logger.error(f"Failed to set fan {fan_id} to mode {mode}: {result['error']}")
            continue
        
        logger.info(f"Fan {fan_id} set to mode {mode} ({MODE_DESCRIPTIONS.get(mode, 'Unknown')})") 
        
        # Apply curve points only for mode 2 (Manual Curve)
        if mode == 2:
            curves = config.get("curves", {}).get(str(fan_id), [])
            for point in curves:
                run_helper(
                    "set_curve",
                    fan_id,
                    point["point"],
                    point["temp"],
                    point["pwm"]
                )
    
    logger.info("Per-fan settings applied")


# Connection manager for WebSocket
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)


manager = ConnectionManager()
config = load_config()


# Background task for broadcasting status
async def status_broadcaster():
    """Broadcast fan status every second."""
    while True:
        try:
            if manager.active_connections:
                status = run_helper("get_status")
                status["config_mode"] = config.get("mode", "auto")
                status["fan_names"] = config.get("fan_names", {})
                await manager.broadcast(status)
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    global config
    config = load_config()
    apply_saved_settings()
    
    # Start software controller
    software_controller.start(config)
    
    # Start background broadcaster
    broadcast_task = asyncio.create_task(status_broadcaster())
    logger.info("Fan Control API started")
    
    yield
    
    # Shutdown
    software_controller.stop()
    broadcast_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass
    logger.info("Fan Control API stopped")


app = FastAPI(title="Fan Control API", lifespan=lifespan)


# Pydantic models
class ModeRequest(BaseModel):
    mode: str  # "auto" or "manual"


class CurvePoint(BaseModel):
    point: int
    temp: float
    pwm: int


class CurveRequest(BaseModel):
    fan_id: int
    curve: list[CurvePoint]


class FanNameRequest(BaseModel):
    fan_id: int
    name: str


class ManualPWMRequest(BaseModel):
    fan_id: int
    pwm: int


class PWMModeRequest(BaseModel):
    fan_id: int
    pwm_mode: int  # 0=DC, 1=PWM


class FanModeRequest(BaseModel):
    fan_id: int
    mode: int  # 0-5


class TargetRPMRequest(BaseModel):
    fan_id: int
    target_rpm: int


class TempSourceRequest(BaseModel):
    fan_id: int
    temp_source: int  # 1-12


class SoftwareControlRequest(BaseModel):
    fan_id: int
    enabled: bool
    temp_source: Optional[str] = None  # Path to temp sensor
    curve: Optional[list] = None  # Curve points


class AutoTuneRequest(BaseModel):
    fan_ids: list  # List of fan IDs to tune
    temp_source: str  # Temperature sensor path
    profile: str = "balanced"  # silent, balanced, or performance


# API Routes
@app.get("/api/status")
async def get_status():
    """Get current fan status."""
    status = run_helper("get_status")
    status["fan_modes"] = config.get("fan_modes", {})
    status["fan_names"] = config.get("fan_names", {})
    status["curves"] = config.get("curves", {})
    status["pwm_modes"] = config.get("pwm_modes", {})
    return status


@app.post("/api/mode")
async def set_mode(request: ModeRequest):
    """Set global mode (auto/manual) - DEPRECATED, use /api/fan_mode instead.
    
    This endpoint is maintained for backward compatibility.
    It sets all fans to the same mode.
    """
    global config
    
    if request.mode not in ["auto", "manual"]:
        raise HTTPException(400, "Invalid mode. Use 'auto' or 'manual'")
    
    # Convert to hardware mode
    hw_mode = 5 if request.mode == "auto" else 1
    results = []
    
    # Update all fans to the same mode
    config.setdefault("fan_modes", {})
    for fan_id in range(1, 6):
        config["fan_modes"][str(fan_id)] = hw_mode
        result = run_helper("set_mode", fan_id, hw_mode)
        results.append(result)
        
        # If switching to manual, apply saved curves
        if request.mode == "manual" and "error" not in result:
            curves = config.get("curves", {}).get(str(fan_id), [])
            for point in curves:
                run_helper(
                    "set_curve",
                    fan_id,
                    point["point"],
                    point["temp"],
                    point["pwm"]
                )
    
    save_config(config)
    return {"success": True, "mode": request.mode, "hw_mode": hw_mode, "results": results}


@app.post("/api/curve")
async def set_curve(request: CurveRequest):
    """Set fan curve for a specific fan."""
    global config
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    if len(request.curve) != 5:
        raise HTTPException(400, "Curve must have exactly 5 points")
    
    # Validate curve is monotonic
    for i in range(1, len(request.curve)):
        if request.curve[i].temp < request.curve[i-1].temp:
            raise HTTPException(400, "Curve temperatures must be increasing")
        if request.curve[i].pwm < request.curve[i-1].pwm:
            raise HTTPException(400, "Curve PWM values must be increasing")
    
    # Save to config
    config.setdefault("curves", {})
    config["curves"][str(request.fan_id)] = [
        {"point": p.point, "temp": p.temp, "pwm": p.pwm}
        for p in request.curve
    ]
    save_config(config)
    
    # Apply curve to hardware if fan is in mode 2 (Manual Curve)
    fan_mode = config.get("fan_modes", {}).get(str(request.fan_id), 5)
    results = []
    
    if fan_mode == 2:
        logger.info(f"Applying curve for fan {request.fan_id} (mode 2)")
        for point in request.curve:
            result = run_helper(
                "set_curve",
                request.fan_id,
                point.point,
                point.temp,
                point.pwm
            )
            results.append(result)
            if "error" in result:
                logger.error(f"Failed to set curve point {point.point}: {result['error']}")
    else:
        logger.info(f"Fan {request.fan_id} is in mode {fan_mode}, not applying curve to hardware")
    
    return {"success": True, "fan_id": request.fan_id, "results": results}


@app.post("/api/fan_name")
async def set_fan_name(request: FanNameRequest):
    """Set custom name for a fan."""
    global config
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    config.setdefault("fan_names", {})
    config["fan_names"][str(request.fan_id)] = request.name
    save_config(config)
    
    return {"success": True, "fan_id": request.fan_id, "name": request.name}


@app.post("/api/pwm_mode")
async def set_pwm_mode(request: PWMModeRequest):
    """Set PWM mode for a fan (0=DC, 1=PWM)."""
    global config
    
    logger.info(f"PWM mode change request: fan_id={request.fan_id}, pwm_mode={request.pwm_mode}")
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    if request.pwm_mode not in [0, 1]:
        raise HTTPException(400, "Invalid pwm_mode. Use 0 for DC or 1 for PWM")
    
    # Apply the PWM mode
    result = run_helper("set_pwm_mode", request.fan_id, request.pwm_mode)
    logger.info(f"Helper result: {result}")
    
    if "error" in result:
        logger.error(f"Failed to set PWM mode: {result['error']}")
        raise HTTPException(500, result["error"])
    
    # Save to config
    config.setdefault("pwm_modes", {})
    config["pwm_modes"][str(request.fan_id)] = request.pwm_mode
    save_config(config)
    
    logger.info(f"PWM mode set successfully for fan {request.fan_id}: {request.pwm_mode}")
    return {"success": True, "fan_id": request.fan_id, "pwm_mode": request.pwm_mode}


@app.post("/api/manual_pwm")
async def set_manual_pwm(request: ManualPWMRequest):
    """Set manual PWM value for a fan (mode 1)."""
    global config
    
    logger.info(f"Manual PWM request: fan_id={request.fan_id}, pwm={request.pwm}")
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    if request.pwm < 0 or request.pwm > 255:
        raise HTTPException(400, "Invalid PWM value. Must be 0-255")
    
    # Set the PWM value
    result = run_helper("set_pwm", request.fan_id, request.pwm)
    logger.info(f"Helper result: {result}")
    
    if "error" in result:
        logger.error(f"Failed to set manual PWM: {result['error']}")
        raise HTTPException(500, result["error"])
    
    # Save to config
    config.setdefault("manual_pwm", {})
    config["manual_pwm"][str(request.fan_id)] = request.pwm
    save_config(config)
    
    logger.info(f"Manual PWM set for fan {request.fan_id}: {request.pwm}")
    return {"success": True, "fan_id": request.fan_id, "pwm": request.pwm}


@app.post("/api/fan_mode")
async def set_fan_mode(request: FanModeRequest):
    """Set mode for a specific fan (0-5)."""
    global config
    
    logger.info(f"Fan mode change request: fan_id={request.fan_id}, mode={request.mode}")
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    if request.mode not in [0, 1, 2, 3, 5]:
        raise HTTPException(400, "Invalid mode. Must be 0, 1, 2, 3, or 5 (mode 4 not supported)")
    
    # Apply the mode
    result = run_helper("set_mode", request.fan_id, request.mode)
    logger.info(f"Helper result: {result}")
    
    if "error" in result:
        logger.error(f"Failed to set fan mode: {result['error']}")
        raise HTTPException(500, result["error"])
    
    # Save to config
    config.setdefault("fan_modes", {})
    config["fan_modes"][str(request.fan_id)] = request.mode
    save_config(config)
    
    # Apply curves only for mode 2 (Manual Curve)
    if request.mode == 2:
        curves = config.get("curves", {}).get(str(request.fan_id), [])
        for point in curves:
            run_helper(
                "set_curve",
                request.fan_id,
                point["point"],
                point["temp"],
                point["pwm"]
            )
    
    logger.info(f"Fan {request.fan_id} mode set to {request.mode} ({MODE_DESCRIPTIONS.get(request.mode, 'Unknown')})")
    return {
        "success": True, 
        "fan_id": request.fan_id, 
        "mode": request.mode,
        "mode_name": MODE_DESCRIPTIONS.get(request.mode, "Unknown")
    }


@app.post("/api/target_rpm")
async def set_target_rpm(request: TargetRPMRequest):
    """Set target RPM for a fan (mode 3)."""
    global config
    
    logger.info(f"Target RPM request: fan_id={request.fan_id}, target_rpm={request.target_rpm}")
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    if request.target_rpm < 0 or request.target_rpm > 10000:
        raise HTTPException(400, "Invalid target RPM. Must be 0-10000")
    
    # Set the target RPM
    result = run_helper("set_target_rpm", request.fan_id, request.target_rpm)
    logger.info(f"Helper result: {result}")
    
    if "error" in result:
        logger.error(f"Failed to set target RPM: {result['error']}")
        raise HTTPException(500, result["error"])
    
    # Save to config
    config.setdefault("target_rpm", {})
    config["target_rpm"][str(request.fan_id)] = request.target_rpm
    save_config(config)
    
    logger.info(f"Target RPM set for fan {request.fan_id}: {request.target_rpm}")
    return {"success": True, "fan_id": request.fan_id, "target_rpm": request.target_rpm}


@app.post("/api/temp_source")
async def set_temp_source(request: TempSourceRequest):
    """Set temperature source for a fan."""
    global config
    
    logger.info(f"Temp source request: fan_id={request.fan_id}, temp_source={request.temp_source}")
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    if request.temp_source < 1 or request.temp_source > 12:
        raise HTTPException(400, "Invalid temp_source. Must be 1-12")
    
    # Set the temp source
    result = run_helper("set_temp_source", request.fan_id, request.temp_source)
    logger.info(f"Helper result: {result}")
    
    if "error" in result:
        logger.error(f"Failed to set temp source: {result['error']}")
        raise HTTPException(500, result["error"])
    
    # Save to config
    config.setdefault("temp_sources", {})
    config["temp_sources"][str(request.fan_id)] = request.temp_source
    save_config(config)
    
    logger.info(f"Temp source set for fan {request.fan_id}: {request.temp_source}")
    return {"success": True, "fan_id": request.fan_id, "temp_source": request.temp_source}


@app.get("/api/temp_sensors")
async def get_temp_sensors():
    """Get list of available temperature sensors."""
    sensors = []
    
    # Get NCT6779 temp sensors
    hwmon_path = None
    for hwmon in glob.glob("/sys/class/hwmon/hwmon*"):
        name_file = os.path.join(hwmon, "name")
        if os.path.exists(name_file):
            with open(name_file) as f:
                if "nct6779" in f.read():
                    hwmon_path = hwmon
                    break
    
    if hwmon_path:
        for i in range(1, 13):  # NCT6779 only supports temp sources 1-12
            label_file = os.path.join(hwmon_path, f"temp{i}_label")
            if os.path.exists(label_file):
                try:
                    with open(label_file) as f:
                        label = f.read().strip()
                        sensors.append({"id": i, "label": label})
                except:
                    pass
    
    return {"sensors": sensors}


@app.get("/api/available_modes")
async def get_available_modes():
    """Get list of available fan control modes."""
    return {
        "modes": [
            {"value": 0, "name": "Off", "description": "Fan stopped (use with caution)"},
            {"value": 1, "name": "Manual PWM", "description": "Set fixed PWM percentage"},
            {"value": 2, "name": "Manual Curve", "description": "Temperature-based automatic control"},
            {"value": 3, "name": "Target RPM", "description": "Maintain target fan speed"},
            {"value": 5, "name": "BIOS Control", "description": "Let motherboard BIOS control fan"}
        ]
    }


@app.post("/api/software_control")
async def set_software_control(request: SoftwareControlRequest):
    """Enable/disable software-based fan control."""
    global config
    
    logger.info(f"Software control request: fan_id={request.fan_id}, enabled={request.enabled}")
    
    if request.fan_id < 1 or request.fan_id > 5:
        raise HTTPException(400, "Invalid fan_id. Must be 1-5")
    
    # Initialize software_control in config if not exists
    config.setdefault("software_control", {})
    
    if request.enabled:
        # Enable software control
        if not request.temp_source:
            raise HTTPException(400, "temp_source required when enabling software control")
        
        # Use existing curve or provided curve
        curve = request.curve if request.curve else config.get("curves", {}).get(str(request.fan_id), [])
        
        config["software_control"][str(request.fan_id)] = {
            "enabled": True,
            "temp_source": request.temp_source,
            "curve": curve
        }
        
        # Set fan to mode 1 (manual) for software control
        result = run_helper("set_mode", request.fan_id, 1)
        if "error" in result:
            raise HTTPException(500, f"Failed to set fan mode: {result['error']}")
        
        config["fan_modes"][str(request.fan_id)] = 1
        
        logger.info(f"Software control enabled for fan {request.fan_id} using {request.temp_source}")
    else:
        # Disable software control
        if str(request.fan_id) in config["software_control"]:
            del config["software_control"][str(request.fan_id)]
        
        logger.info(f"Software control disabled for fan {request.fan_id}")
    
    save_config(config)
    
    # Restart software controller to pick up changes
    software_controller.stop()
    await asyncio.sleep(0.1)
    software_controller.start(config)
    
    return {"success": True, "fan_id": request.fan_id, "enabled": request.enabled}


@app.get("/api/temp_sensors_all")
async def get_all_temp_sensors():
    """Get all available temperature sensors from all hwmon devices."""
    sensors = []
    
    # Scan all hwmon devices
    for hwmon_path in glob.glob("/sys/class/hwmon/hwmon*"):
        name_file = os.path.join(hwmon_path, "name")
        if not os.path.exists(name_file):
            continue
        
        try:
            with open(name_file) as f:
                hwmon_name = f.read().strip()
            
            # Find all temp sensors in this hwmon
            for temp_file in glob.glob(os.path.join(hwmon_path, "temp*_input")):
                temp_num = temp_file.split("temp")[1].split("_")[0]
                label_file = os.path.join(hwmon_path, f"temp{temp_num}_label")
                
                label = f"temp{temp_num}"
                if os.path.exists(label_file):
                    try:
                        with open(label_file) as f:
                            label = f.read().strip()
                    except:
                        pass
                
                sensors.append({
                    "path": temp_file,
                    "hwmon": hwmon_name,
                    "label": label,
                    "display_name": f"{hwmon_name} - {label}"
                })
        except Exception as e:
            logger.error(f"Error reading hwmon {hwmon_path}: {e}")
            continue
    
    return {"sensors": sensors}


@app.get("/api/config")
async def get_config():
    """Get current config."""
    return config


@app.post("/api/auto_tune/start")
async def start_auto_tune(request: AutoTuneRequest):
    """Start auto-tuning process."""
    global auto_tuner
    
    if auto_tuner.running:
        raise HTTPException(400, "Auto-tune already running")
    
    logger.info(f"Starting auto-tune for fans {request.fan_ids} with profile {request.profile}")
    
    # Start auto-tune in background
    asyncio.create_task(auto_tuner.run_auto_tune(request.fan_ids, request.temp_source, request.profile))
    
    return {"success": True, "message": "Auto-tune started"}


@app.get("/api/auto_tune/status")
async def get_auto_tune_status():
    """Get current auto-tuning progress."""
    return {
        "running": auto_tuner.running,
        "progress": auto_tuner.progress,
        "current_action": auto_tuner.current_action,
        "results": auto_tuner.results if not auto_tuner.running else {}
    }


@app.post("/api/auto_tune/apply")
async def apply_auto_tune():
    """Apply generated curves from auto-tune."""
    global config
    
    if auto_tuner.running:
        raise HTTPException(400, "Auto-tune still running")
    
    if not auto_tuner.results or "generated_curves" not in auto_tuner.results:
        raise HTTPException(400, "No auto-tune results available")
    
    # Apply curves to config
    for fan_id_str, curve in auto_tuner.results["generated_curves"].items():
        config.setdefault("curves", {})
        config["curves"][fan_id_str] = curve
    
    # Store calibration data
    config["auto_tune_results"] = {
        "timestamp": auto_tuner.results.get("timestamp"),
        "profile": auto_tuner.results.get("profile"),
        "fan_calibration": auto_tuner.results.get("fan_calibration", {}),
        "temp_profile": auto_tuner.results.get("temp_profile", {})
    }
    
    save_config(config)
    
    logger.info("Auto-tune curves applied")
    return {"success": True, "message": "Curves applied successfully"}


@app.post("/api/auto_tune/cancel")
async def cancel_auto_tune():
    """Cancel running auto-tune."""
    auto_tuner.cancel()
    return {"success": True, "message": "Auto-tune cancelled"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, handle any incoming messages
            data = await websocket.receive_text()
            # Could handle commands here if needed
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# Serve frontend
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
    
    @app.get("/")
    async def serve_frontend():
        return FileResponse(FRONTEND_DIR / "index.html")
    
    @app.get("/{path:path}")
    async def serve_frontend_fallback(path: str):
        # API routes are handled above, this catches frontend routes
        file_path = FRONTEND_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
else:
    @app.get("/")
    async def no_frontend():
        return {"message": "Frontend not built. Run 'npm run build' in frontend directory."}
