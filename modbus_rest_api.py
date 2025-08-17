from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
import socket
import struct
import asyncio
from contextlib import asynccontextmanager
from time import sleep
from solar_charging import regulate_ev_charging
from modbus_interaction import write_modbus_data, read_sma_modbus_data, read_wallbox_modbus_data, sma_devices, ev_charging_modbus_registers


UDP_IP = "192.168.188.39"
UDP_PORT = 9522

REGULATION_DELAY = 0.5 # Delay between loop iterations for getting some udp/modbus data and adjustion the ev charging current


app = FastAPI()

# Global variables for UDP data
grid_power = 0 # in W, negative == feed_in, positive == consumption
emeter_power = 0 # in W, positive == production
battery_power = 0 # in W, negative = charging, positive = discharging
battery_SoC = 0 # percentage, int
ev_charging_state = 0 # 1-6
ev_max_current = 0 # 0-16
is_solar_only_charging = True # Boolean, if instant charging or solar charging is activated

charging_states = {
    0: "Not available",
    1: "A: EV disconnected",
    2: "B: EV connected",
    3: "C: EV charge",
    4: "D: EV charge (ventilation required)",
    5: "E: Error condition",
    6: "F: Fault condition"
}

# Allowed CORS origins hinzufügen
origins = [
    "http://localhost:4200",  # Erlaube die Verbindung von Angular-Frontend
    "http://127.0.0.1:4200",  # Falls du von einer anderen IP aus zugreifst
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
    global grid_power, emeter_power, battery_power, battery_SoC, ev_charging_state, ev_max_current, charging_states
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
    
    return data


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
        get_grid_and_emeter_power(loop, sock)
        get_battery_power_and_soc()

        if is_solar_only_charging:
            regulate_ev_charging()
        else: 
            ev_max_current = read_wallbox_modbus_data(**sma_devices["maximum_current"])
            if (ev_max_current != 16):
                write_modbus_data(**ev_charging_modbus_registers["maximum_current"], value=16)
        
        sleep(REGULATION_DELAY)


# getting grid and emeter power
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
