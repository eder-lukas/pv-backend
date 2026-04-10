from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field
import logging
from starlette.middleware.cors import CORSMiddleware
import socket
import struct
import asyncio
from contextlib import asynccontextmanager
from solar_charging import regulate_ev_charging, charging_states, MAX_CHARGING_CURRENT
from modbus_interaction import (
    write_modbus_data,
    read_sma_modbus_data,
    read_wallbox_modbus_data,
    sma_devices,
)
import shared_state
from wallbox_config import WALLBOX_CONFIGS
from wallbox_service import get_wallbox_config, get_wallbox_name

# Change this address to the local interface address
UDP_IP = "192.168.188.205"
UDP_PORT = 9522

EV_CHARGING_REGULATION_DELAY = (
    10  # Delay between loop iterations for adjusting the ev charging current
)


app = FastAPI()

# Allowed CORS origins
origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "http://192.168.188.205:4200",
]


# FastAPI lifespan event to manage background tasks
@asynccontextmanager
async def lifespan(app: FastAPI):
    data_collection_task = asyncio.create_task(data_collection())
    ev_regulation_task = asyncio.create_task(ev_charging_regulation())

    yield  # API runs while this runs in the background

    # Stop both tasks when API shuts down
    data_collection_task.cancel()
    ev_regulation_task.cancel()

    # Wait for tasks to finish cancellation
    try:
        await data_collection_task
    except asyncio.CancelledError:
        pass
    try:
        await ev_regulation_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=[
        "GET",
        "POST",
    ],  # Allow GET for getting and POST for setting solar-charging-only bool
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@app.get("/solar-data")
def get_power_data():
    global charging_states
    data = {}

    # Read solar power data
    data["tripower_power"] = read_sma_modbus_data(**sma_devices["tripower_total_power"])
    data["tripower_str1_power"] = read_sma_modbus_data(
        **sma_devices["tripower_str1_power"]
    )
    data["tripower_str2_power"] = read_sma_modbus_data(
        **sma_devices["tripower_str2_power"]
    )
    data["tripower_str3_power"] = read_sma_modbus_data(
        **sma_devices["tripower_str3_power"]
    )

    # Use global data (updated by background task)
    data["battery_power"] = shared_state.battery_power
    data["battery_SoC"] = shared_state.battery_SoC

    data["grid_power"] = round(shared_state.grid_power / 10)
    data["emeter_power"] = round(shared_state.emeter_power / 10)

    data["wallboxes"] = [
        {
            "id": wb_id,
            "name": get_wallbox_name(wb_id),
            "charging_state": charging_states.get(wb["charging_state"], "Unknown"),
            "maximum_current": wb["maximum_current"],
            "solar_only_charging": wb["solar_only_charging"],
            "number_of_phases_used": wb["number_of_phases_used"],
            "priority": wb["priority"],
        }
        for wb_id, wb in shared_state.wallbox_states.items()
    ]

    # Calculate house power
    data["consumption"] = (
        (data["tripower_power"] or 0)
        + (data["emeter_power"] or 0)
        + (data["grid_power"] or 0)
        + (data["battery_power"] or 0)
    )

    data["home_bat_min_soc"] = shared_state.home_bat_min_soc

    return data


@app.post("/solar-only-charging")
def set_solar_only_charging(
    wallbox: int = Query(
        ...,
        description="ID of the wallbox which should be set to solar only charging or immediate charging",
    ),
    enable: bool = Query(
        ..., description="True = Nur Solarstrom laden, False = normaler Betrieb"
    ),
):
    wb = shared_state.wallbox_states.get(wallbox)

    if not wb:
        return {"success": False, "error": "Wallbox not found"}

    wb["solar_only_charging"] = enable

    return {"success": True, "solar_only_charging": enable}


class HomeBatMinSocRequest(BaseModel):
    value: int = Field(
        ..., ge=0, le=100, description="Minimaler SOC der Hausbatterie (0–100)"
    )

@app.post("/home-bat-min-soc")
def set_home_bat_min_soc(payload: HomeBatMinSocRequest):
    shared_state.home_bat_min_soc = payload.value
    return {"home_bat_min_soc": shared_state.home_bat_min_soc}


@app.post("/wallbox/{wallbox_id}/number_of_phases_used")
def set_number_of_phases_used(
    wallbox_id: int, number_of_phases_used: int = Query(..., description="1, 2 oder 3 Phasen")
):
    if number_of_phases_used not in (1, 2, 3):
        raise HTTPException(
            status_code=400, detail="number_of_phases_used must be 1, 2, or 3"
        )

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

    # Priorität um 1 erhöhen (kleiner Wert = höhere Priorität)
    if wb["priority"] > 1:
        wb["priority"] -= 1

        # Alle anderen Wallboxen ggf. nachjustieren
        for other_id, other_wb in shared_state.wallbox_states.items():
            if other_id != wallbox_id and other_wb["priority"] == wb["priority"]:
                other_wb["priority"] += 1

    return {"wallbox_id": wallbox_id, "priority": wb["priority"]}


@app.post("/wallbox/{wallbox_id}/decrease_priority")
def decrease_priority(wallbox_id: int):
    wb = shared_state.wallbox_states.get(wallbox_id)
    if not wb:
        raise HTTPException(status_code=404, detail="Wallbox not found")

    max_priority = len(shared_state.wallbox_states)
    if wb["priority"] < max_priority:
        wb["priority"] += 1

        # Alle anderen Wallboxen ggf. nachjustieren
        for other_id, other_wb in shared_state.wallbox_states.items():
            if other_id != wallbox_id and other_wb["priority"] == wb["priority"]:
                other_wb["priority"] -= 1

    return {"wallbox_id": wallbox_id, "priority": wb["priority"]}


# Background task for data collection (UDP and Modbus data)
# Collects grid and emeter power information from udp messages and battery power and SoC information via modbus
async def data_collection():
    logger.info("✅ Data collection task started...")

    loop = asyncio.get_running_loop()
    sock = None

    while True:
        try:
            # (Re)create socket if needed
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.setblocking(False)
                    sock.bind((UDP_IP, UDP_PORT))
                    logger.info(f"✅ UDP server running on {UDP_IP}:{UDP_PORT}")
                except OSError as e:
                    logger.error(f"⚠️ UDP bind failed ({e}), retrying in 10s")
                    if sock:
                        sock.close()
                        sock = None
                    await asyncio.sleep(10)
                    continue  # retry later

            await get_grid_and_emeter_power(loop, sock)
            get_battery_power_and_soc()
            get_ev_charging_data()

        except asyncio.CancelledError:
            logger.warn("🛑 Data collection task cancelled")
            if sock:
                sock.close()
            raise
        except Exception as e:
            logger.error(f"⚠️ Error in data collection task: {e}")
            await asyncio.sleep(1)


# Background task for EV charging regulation
async def ev_charging_regulation():
    logger.info("✅ EV charging regulation task started...")
    while True:
        try:
            # Order Wallboxes by Priority
            sorted_wallboxes = sorted(
                shared_state.wallbox_states.items(),
                key=lambda item: item[1]["priority"],
            )

            for wb_id, wb_state in sorted_wallboxes:
                config = get_wallbox_config(wb_id)

                if wb_state["solar_only_charging"]:
                    regulate_ev_charging(config, wb_state)
                else:
                    current = read_wallbox_modbus_data(**config["maximum_current"])
                    # Set charging current to maximum when not in solar-only mode

                    if current != MAX_CHARGING_CURRENT:
                        logger.info(
                            f"Enabled instant charging. Setting charging current to {MAX_CHARGING_CURRENT}"
                        )
                        wb_state["maximum_current"] = MAX_CHARGING_CURRENT
                        write_modbus_data(
                            **config["maximum_current"], value=MAX_CHARGING_CURRENT
                        )

                await asyncio.sleep(EV_CHARGING_REGULATION_DELAY)

        except asyncio.CancelledError:
            logger.warning("🛑 EV charging regulation task cancelled")
            raise
        except Exception as e:
            logger.error(f"⚠️ Error in EV charging regulation task: {e}")


# getting grid and emeter power from UDP packets
async def get_grid_and_emeter_power(loop, sock):
    try:
        data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 1024), timeout=1)
    except asyncio.TimeoutError:
        return  # no data in this cycle
    except OSError as e:
        logger.warning(f"⚠️ UDP socket error: {e}")
        return

    try:
        if data[:3] != b"SMA":  # Check if the packet is from SMA
            return

        ip, _ = addr

        if ip == "192.168.188.54":  # Grid meter
            feed_in = struct.unpack(">I", data[52:56])[0]
            if feed_in == 0:
                shared_state.grid_power = struct.unpack(">I", data[32:36])[0]
            else:
                shared_state.grid_power = -1 * feed_in

        elif ip == "192.168.188.87":  # Energy meter
            shared_state.emeter_power = struct.unpack(">I", data[52:56])[0]

    except Exception as e:
        logger.error(f"⚠️ Error in UDP server: {e}")


def get_battery_power_and_soc():
    try:
        new_battery_power = read_sma_modbus_data(**sma_devices["battery_power"])
        new_battery_soc = read_sma_modbus_data(**sma_devices["battery_SoC"])

        if new_battery_power is not None:
            shared_state.battery_power = new_battery_power

        if new_battery_soc is not None:
            shared_state.battery_SoC = new_battery_soc

    except Exception as e:
        logger.warning(f"⚠️ Battery Modbus read failed: {e}")


def get_ev_charging_data():
    try:
        for wb_id, wb_state in shared_state.wallbox_states.items():
            config = get_wallbox_config(wb_id)

            state = read_wallbox_modbus_data(**config["charging_state"])
            current = read_wallbox_modbus_data(**config["maximum_current"])

            if state is not None:
                wb_state["charging_state"] = state

            if current is not None:
                wb_state["maximum_current"] = current

    except Exception as e:
        logger.warning(f"⚠️ EVSE Modbus read failed: {e}")


# Running:
# uvicorn rest_api:app --host localhost --port 8000 [--reload]
