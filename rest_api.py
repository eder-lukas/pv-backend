"""
rest_api.py

FastAPI backend for solar EV charging management.

Run with:
  uvicorn rest_api:app --host 0.0.0.0 --port 8000 [--reload]
"""

import asyncio
import logging
import socket
import struct
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

import shared_state
from modbus_interaction import (
    read_sma_modbus_data,
    sma_devices,
)
from solar_charging import (
    regulate_all_wallboxes_solar,
    CHARGING_STATES,
    MAX_CHARGING_CURRENT,
)
from wallbox.wallbox_config import WALLBOXES
from wallbox.wallbox_base import WallboxBase

# ── Network config ─────────────────────────────────────────────────────────────
UDP_IP   = "192.168.188.205"
UDP_PORT = 9522

# Seconds between full regulation cycles (increase path already has a per-wallbox
# 10-second wait built in; this is the outer loop cadence)
EV_CHARGING_REGULATION_DELAY = 10

# ── CORS ───────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "http://192.168.188.205:4200",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(data_collection(), name="data_collection"),
        asyncio.create_task(ev_charging_regulation(), name="ev_regulation"),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.get("/solar-data")
def get_power_data():
    data: dict = {}

    data["tripower_power"]      = read_sma_modbus_data(**sma_devices["tripower_total_power"])
    data["tripower_str1_power"] = read_sma_modbus_data(**sma_devices["tripower_str1_power"])
    data["tripower_str2_power"] = read_sma_modbus_data(**sma_devices["tripower_str2_power"])
    data["tripower_str3_power"] = read_sma_modbus_data(**sma_devices["tripower_str3_power"])

    data["battery_power"] = shared_state.battery_power
    data["battery_SoC"]   = shared_state.battery_SoC
    data["grid_power"]    = round(shared_state.grid_power / 10)
    data["emeter_power"]  = round(shared_state.emeter_power / 10)

    data["wallboxes"] = [
        {
            "id":                    wb_id,
            "name":                  WALLBOXES[wb_id].name,
            "charging_state":        CHARGING_STATES.get(wb["charging_state"], "Unknown"),
            "maximum_current":       wb["maximum_current"],
            "solar_only_charging":   wb["solar_only_charging"],
            "number_of_phases_used": wb["number_of_phases_used"],
            "priority":              wb["priority"],
            "paused":                wb["paused"],
        }
        for wb_id, wb in shared_state.wallbox_states.items()
        if wb_id in WALLBOXES
    ]

    data["consumption"] = (
        (data["tripower_power"] or 0)
        + (data["emeter_power"]  or 0)
        + (data["grid_power"]    or 0)
        + (data["battery_power"] or 0)
    )
    data["home_bat_min_soc"] = shared_state.home_bat_min_soc

    return data


@app.post("/solar-only-charging")
def set_solar_only_charging(
    wallbox: int = Query(..., description="Wallbox ID"),
    enable: bool = Query(..., description="True = solar-only, False = instant charging"),
):
    wb = shared_state.wallbox_states.get(wallbox)
    if not wb:
        raise HTTPException(status_code=404, detail="Wallbox not found")
    wb["solar_only_charging"] = enable
    return {"success": True, "solar_only_charging": enable}


class HomeBatMinSocRequest(BaseModel):
    value: int = Field(..., ge=0, le=100, description="Minimum home battery SoC (0–100)")


@app.post("/home-bat-min-soc")
def set_home_bat_min_soc(payload: HomeBatMinSocRequest):
    shared_state.home_bat_min_soc = payload.value
    return {"home_bat_min_soc": shared_state.home_bat_min_soc}


@app.post("/wallbox/{wallbox_id}/number_of_phases_used")
def set_number_of_phases_used(
    wallbox_id: int,
    number_of_phases_used: int = Query(..., description="1, 2, or 3 phases"),
):
    if number_of_phases_used not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="number_of_phases_used must be 1, 2, or 3")
    wb = shared_state.wallbox_states.get(wallbox_id)
    if not wb:
        raise HTTPException(status_code=404, detail="Wallbox not found")
    wb["number_of_phases_used"] = number_of_phases_used
    return {"wallbox_id": wallbox_id, "number_of_phases_used": number_of_phases_used}


@app.post("/wallbox/{wallbox_id}/increase_priority")
def increase_priority(wallbox_id: int):
    wb = shared_state.wallbox_states.get(wallbox_id)
    if not wb:
        raise HTTPException(status_code=404, detail="Wallbox not found")
    if wb["priority"] > 1:
        wb["priority"] -= 1
        for oid, owb in shared_state.wallbox_states.items():
            if oid != wallbox_id and owb["priority"] == wb["priority"]:
                owb["priority"] += 1
    return {"wallbox_id": wallbox_id, "priority": wb["priority"]}


@app.post("/wallbox/{wallbox_id}/decrease_priority")
def decrease_priority(wallbox_id: int):
    wb = shared_state.wallbox_states.get(wallbox_id)
    if not wb:
        raise HTTPException(status_code=404, detail="Wallbox not found")
    max_prio = len(shared_state.wallbox_states)
    if wb["priority"] < max_prio:
        wb["priority"] += 1
        for oid, owb in shared_state.wallbox_states.items():
            if oid != wallbox_id and owb["priority"] == wb["priority"]:
                owb["priority"] -= 1
    return {"wallbox_id": wallbox_id, "priority": wb["priority"]}


class SetMaxCurrentRequest(BaseModel):
    value: int = Field(..., ge=6000, le=16000, description="Max charging current in A")


@app.post("/wallbox/{wallbox_id}/max_current")
def set_max_current(wallbox_id: int, payload: SetMaxCurrentRequest):
    wb = shared_state.wallbox_states.get(wallbox_id)
    if not wb:
        raise HTTPException(status_code=404, detail="Wallbox not found")

    # Only allow for instant charging
    if wb.get("solar_only_charging", False):
        raise HTTPException(
            status_code=400,
            detail="Cannot set max current while solar-only charging is enabled"
        )

    wallbox: WallboxBase = WALLBOXES.get(wallbox_id)
    if wallbox is None:
        raise HTTPException(status_code=500, detail="Wallbox config missing")

    try:
        # Apply immediately
        wallbox.write_max_current(payload.value)

        # Update shared state (important so background loop keeps it)
        wb["maximum_current"] = payload.value

        return {
            "wallbox_id": wallbox_id,
            "maximum_current": payload.value,
        }

    except Exception as e:
        logger.error(f"Error setting max current: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Background tasks ───────────────────────────────────────────────────────────

async def data_collection():
    """
    background task to get get grid, emeter, battery power and battery soc data and write it to the shared state
    """
    logger.info("✅ Data collection task started")
    loop = asyncio.get_running_loop()
    sock = None

    while True:
        try:
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.setblocking(False)
                    sock.bind((UDP_IP, UDP_PORT))
                    logger.info(f"✅ UDP server on {UDP_IP}:{UDP_PORT}")
                except OSError as e:
                    logger.error(f"⚠️ UDP bind failed: {e} – retrying in 10 s")
                    if sock:
                        sock.close()
                    sock = None
                    await asyncio.sleep(10)
                    continue

            await _get_grid_and_emeter_power(loop, sock)
            _get_battery_power_and_soc()

        except asyncio.CancelledError:
            logger.warning("🛑 Data collection cancelled")
            if sock:
                sock.close()
            raise
        except Exception as e:
            logger.error(f"⚠️ Data collection error: {e}")
            await asyncio.sleep(1)


async def ev_charging_regulation():
    """
    background task to 
    """
    logger.info("✅ EV charging regulation task started")
    while True:
        try:
            # ── Solar-only wallboxes ───────────────────────────────────
            await regulate_all_wallboxes_solar(WALLBOXES, shared_state.wallbox_states)

            # ── Instant-charging wallboxes ─────────────────────────────
            for wb_id, wb_state in shared_state.wallbox_states.items():
                if wb_state.get("solar_only_charging", False):
                    continue   # handled above

                wallbox: WallboxBase = WALLBOXES.get(wb_id)
                if wallbox is None:
                    continue

                current = wallbox.read_max_current()
                target_current = wb_state.get("maximum_current", MAX_CHARGING_CURRENT)
                
                if current != target_current:
                    logger.info(
                        f"[{wallbox.name}] Instant charging: "
                        f"setting current to {target_current} mA"
                    )
                    # Resume first if paused
                    if wb_state.get("paused", False):
                        wallbox.resume_charging()
                        wb_state["paused"] = False
                    wallbox.write_max_current(target_current)
                    wb_state["maximum_current"] = target_current

            await asyncio.sleep(EV_CHARGING_REGULATION_DELAY)

        except asyncio.CancelledError:
            logger.warning("🛑 EV charging regulation cancelled")
            raise
        except Exception as e:
            logger.error(f"⚠️ EV charging regulation error: {e}")
            await asyncio.sleep(1)


async def _get_grid_and_emeter_power(loop, sock):
    try:
        data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 1024), timeout=1)
    except asyncio.TimeoutError:
        return
    except OSError as e:
        logger.warning(f"⚠️ UDP socket error: {e}")
        return

    try:
        if data[:3] != b"SMA":
            return
        ip, _ = addr
        if ip == "192.168.188.54":       # Grid meter
            feed_in = struct.unpack(">I", data[52:56])[0]
            shared_state.grid_power = (
                struct.unpack(">I", data[32:36])[0] if feed_in == 0 else -feed_in
            )
        elif ip == "192.168.188.87":     # Energy meter / PV
            shared_state.emeter_power = struct.unpack(">I", data[52:56])[0]
    except Exception as e:
        logger.error(f"⚠️ UDP parse error: {e}")


def _get_battery_power_and_soc():
    try:
        bp  = read_sma_modbus_data(**sma_devices["battery_power"])
        soc = read_sma_modbus_data(**sma_devices["battery_SoC"])
        if bp  is not None: shared_state.battery_power = bp
        if soc is not None: shared_state.battery_SoC   = soc
    except Exception as e:
        logger.warning(f"⚠️ Battery Modbus error: {e}")
