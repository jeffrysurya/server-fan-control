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
    
    # Start background broadcaster
    broadcast_task = asyncio.create_task(status_broadcaster())
    logger.info("Fan Control API started")
    
    yield
    
    # Shutdown
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
    temp_source: int  # 1-13


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
    
    if request.temp_source < 1 or request.temp_source > 13:
        raise HTTPException(400, "Invalid temp_source. Must be 1-13")
    
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
        for i in range(1, 14):
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


@app.get("/api/config")
async def get_config():
    """Get current config."""
    return config


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
