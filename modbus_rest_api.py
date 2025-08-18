from fastapi import FastAPI, Query
from starlette.middleware.cors import CORSMiddleware
import socket
import struct
import asyncio
from contextlib import asynccontextmanager
from solar_charging import regulate_ev_charging, charging_states
from modbus_interaction import write_modbus_data, read_sma_modbus_data, read_wallbox_modbus_data, sma_devices, ev_charging_modbus_registers
import shared_state


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
    allow_methods=["GET", "POST"],  # Allow GET for getting and POST for setting solar-charging-only bool
    allow_headers=["*"],
)


@app.get("/solar-data")
def get_power_data():
    global charging_states
    data = {}

    # Read solar power data
    data["tripower_power"] = read_sma_modbus_data(**sma_devices["tripower_total_power"])
    data["tripower_str1_power"] = read_sma_modbus_data(**sma_devices["tripower_str1_power"])
    data["tripower_str2_power"] = read_sma_modbus_data(**sma_devices["tripower_str2_power"])
    data["tripower_str3_power"] = read_sma_modbus_data(**sma_devices["tripower_str3_power"])
    
    # Use global data (updated by background task)
    data["battery_power"] = shared_state.battery_power
    data["battery_SoC"] = shared_state.battery_SoC
    
    data["grid_power"] = round(shared_state.grid_power / 10)
    data["emeter_power"] = round(shared_state.emeter_power / 10)

    data["charging_state"] = charging_states.get(shared_state.ev_charging_state, "Unknown")
    data["maximum_current"] = shared_state.ev_max_current
    data["solar_only_charging"] = shared_state.is_solar_only_charging
    
    # Calculate house power
    data["consumption"] = (
        (data["tripower_power"] or 0) + (data["emeter_power"] or 0)
        + (data["grid_power"] or 0) + (data["battery_power"] or 0)
    )

    return data


@app.post("/solar-only-charging")
def set_solar_only_charging(enable: bool = Query(..., description="True = Nur Solarstrom laden, False = normaler Betrieb")):
    shared_state.is_solar_only_charging = enable

    return {
        "success": True,
        "solar_only_charging": shared_state.is_solar_only_charging
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
        try:
            await get_grid_and_emeter_power(loop, sock)
            get_battery_power_and_soc()
            get_ev_charging_data()

            if shared_state.is_solar_only_charging:
                regulate_ev_charging()
            else:
                shared_state.ev_max_current = read_wallbox_modbus_data(**ev_charging_modbus_registers["maximum_current"])
                if (shared_state.ev_max_current != 16):
                    write_modbus_data(**ev_charging_modbus_registers["maximum_current"], value=16)
            
            await asyncio.sleep(REGULATION_DELAY)
        except Exception as e:
            print(f"⚠️ Error in main loop: {e}")

# getting grid and emeter power from UDP packets
async def get_grid_and_emeter_power(loop, sock):
    try:
        data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 1024), timeout=1)
        if data[:3] == b"SMA":  # Check if the packet is from SMA
            ip, _ = addr

            if ip == '192.168.188.54':  # Grid meter
                feed_in = struct.unpack(">I", data[52:56])[0]
                if (feed_in == 0):
                    shared_state.grid_power = struct.unpack(">I", data[32:36])[0]
                else:
                    shared_state.grid_power = -1 * feed_in

            elif ip == '192.168.188.87':  # Energy meter
                shared_state.emeter_power = struct.unpack(">I", data[52:56])[0]

    except Exception as e:
        print(f"⚠️ Error in UDP server: {e}")


def get_battery_power_and_soc():
    try:
        new_battery_power = read_sma_modbus_data(**sma_devices["battery_power"])
        new_battery_soc = read_sma_modbus_data(**sma_devices["battery_SoC"])
        
        if new_battery_power is not None:
            shared_state.battery_power = new_battery_power
        
        if new_battery_soc is not None:
            shared_state.battery_SoC = new_battery_soc
            
    except Exception as e:
        print(f"⚠️ Error reading battery data: {e}")


def get_ev_charging_data():
    try:
        new_charging_state = read_wallbox_modbus_data(**ev_charging_modbus_registers["charging_state"])
        new_max_current = read_wallbox_modbus_data(**ev_charging_modbus_registers["maximum_current"])
        
        if new_charging_state is not None:
            shared_state.ev_charging_state = new_charging_state
        
        if new_max_current is not None:
            shared_state.ev_max_current = new_max_current
            
    except Exception as e:
        print(f"⚠️ Error reading EV charging data: {e}")

# Running:
# uvicorn modbus_rest_api:app --host localhost --port 8000 --reload
