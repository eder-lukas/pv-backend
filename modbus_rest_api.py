from fastapi import FastAPI, Query
from starlette.middleware.cors import CORSMiddleware
import socket
import struct
import asyncio
from contextlib import asynccontextmanager
from time import sleep
from solar_charging import regulate_ev_charging, charging_states
from modbus_interaction import write_modbus_data, read_sma_modbus_data, read_wallbox_modbus_data, sma_devices, ev_charging_modbus_registers
from shared_state import grid_power, emeter_power, battery_power, battery_SoC, ev_charging_state, ev_max_current, is_solar_only_charging


UDP_IP = "192.168.188.39"
UDP_PORT = 9522

REGULATION_DELAY = 0.5 # Delay between loop iterations for getting some udp/modbus data and adjustion the ev charging current


app = FastAPI()

# Allowed CORS origins hinzufügen
origins = [
    "http://localhost:4200",
    "http://127.0.0.1:4200",
    "http://192.168.188.205:4200",
]


# FastAPI lifespan event to manage background tasks
@asynccontextmanager
async def lifespan(app: FastAPI):
    udp_task = asyncio.create_task(async_task())  # Start UDP listener
    yield  # API runs while this runs in the background
    udp_task.cancel()  # Stop UDP listener when API shuts down


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET"],  # Allow get only
    allow_headers=["*"],
)


@app.get("/solar-data")
def get_power_data():
    global grid_power, emeter_power, battery_power, battery_SoC, ev_charging_state, ev_max_current, charging_states, is_solar_only_charging
    data = {}

    # Read solar power data
    data["tripower_power"] = read_sma_modbus_data(**sma_devices["tripower_total_power"])
    data["tripower_str1_power"] = read_sma_modbus_data(**sma_devices["tripower_str1_power"])
    data["tripower_str2_power"] = read_sma_modbus_data(**sma_devices["tripower_str2_power"])
    data["tripower_str3_power"] = read_sma_modbus_data(**sma_devices["tripower_str3_power"])
    
    # Read battery data
    data["battery_power"] = battery_power
    data["battery_SoC"] = battery_SoC
    
    data["grid_power"] = round(grid_power / 10)
    data["emeter_power"] = round(emeter_power / 10)

    # Calculate house power
    data["consumption"] = (
        (data["tripower_power"] or 0) + (data["emeter_power"] or 0)
        + (data["grid_power"] or 0) + (data["battery_power"] or 0)
    )

    # Add EV Charging data
    data["charging_state"] = charging_states[ev_charging_state]
    data["maximum_current"] = ev_max_current
    data["solar_only_charging"] = is_solar_only_charging
    
    return data


@app.post("/solar-only-charging")
def set_solar_only_charging(enable: bool = Query(..., description="True = Nur Solarstrom laden, False = normaler Betrieb")):
    global is_solar_only_charging

    is_solar_only_charging = enable

    return {
        "success": True,
        "solar_only_charging": is_solar_only_charging
    }


# Async while true loop
# Collects grid and emeter power information from udp messages and battery power and SoC information via modbus
# Then starts the ev-charging regulation
async def async_task():
    # UDP socket for grid_power and emeter_power data collection
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.setblocking(False)  # Make socket non-blocking

    print(f"✅ UDP server running on Port {UDP_PORT}...")

    loop = asyncio.get_running_loop()

    while True:
        await get_grid_and_emeter_power(loop, sock)
        get_battery_power_and_soc()

        if is_solar_only_charging:
            regulate_ev_charging()
        else: 
            ev_max_current = read_wallbox_modbus_data(**sma_devices["maximum_current"])
            if (ev_max_current != 16):
                write_modbus_data(**ev_charging_modbus_registers["maximum_current"], value=16)
        
        sleep(REGULATION_DELAY)


# getting grid and emeter power from UDP packets
async def get_grid_and_emeter_power(loop, sock):
    global grid_power, emeter_power
    try:
        data, addr = await loop.sock_recvfrom(sock, 1024)  # Proper async recv
        if data[:3] == b"SMA":  # Check if the packet is from SMA
            ip, _ = addr

            if ip == '192.168.188.54':  # Grid meter
                feed_in = struct.unpack(">I", data[52:56])[0]
                if (feed_in == 0):
                    grid_power = struct.unpack(">I", data[32:36])[0]
                else:
                    grid_power = -1 * feed_in

            if ip == '192.168.188.87':  # Energy meter
                emeter_power = struct.unpack(">I", data[52:56])[0]

    except Exception as e:
        print(f"⚠️ Error in UDP server: {e}")


def get_battery_power_and_soc():
    global battery_power, battery_SoC
    battery_power = read_sma_modbus_data(**sma_devices["battery_power"])
    battery_SoC = read_sma_modbus_data(**sma_devices["battery_SoC"])


# Running:
# uvicorn modbus_rest_api:app --host localhost --port 8000 --reload
