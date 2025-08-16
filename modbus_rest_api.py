from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from pymodbus.client import ModbusTcpClient # older versions pymodbus.client.sync
import socket
import struct
import asyncio
from contextlib import asynccontextmanager
import math

UDP_IP = "192.168.188.39"
UDP_PORT = 9522

TRIPOWER_IP = "192.168.188.45"
SUNNY_ISLAND_IP = "192.168.188.117"
CHARGER_ME_IP = "192.168.188.94"
MODBUS_PORT = 502

app = FastAPI()

# Global variables for UDP data
grid_power = 0 # in W, negative == feed_in, positive == consumption
emeter_power = 0 # in W, positive == production
battery_power = 0 # in W, negative = charging, positive = discharging
battery_SoC = 0 # percentage, int
ev_charging_state = 0 # 1-6
ev_max_current = 0 # 0-16

charging_states = {
    0: "Not available",
    1: "A: EV disconnected",
    2: "B: EV connected",
    3: "C: EV charge",
    4: "D: EV charge (ventilation required)",
    5: "E: Error condition",
    6: "F: Fault condition"
}



# values are in two registers
def combine_registers(high, low):
    return (high << 16) + low

# Device configuration
sma_devices = {
    "tripower_total_power": {"ip": TRIPOWER_IP, "register": 30775, "slave": 3, "signed": False, "nan_value": 0x80000000},
    "tripower_str1_power": {"ip": TRIPOWER_IP, "register": 30773, "slave": 3, "signed": False, "nan_value": 0x80000000},
    "tripower_str2_power": {"ip": TRIPOWER_IP, "register": 30961, "slave": 3, "signed": False, "nan_value": 0x80000000},
    "tripower_str3_power": {"ip": TRIPOWER_IP, "register": 30967, "slave": 3, "signed": False, "nan_value": 0x80000000},

    "battery_power": {"ip": SUNNY_ISLAND_IP, "register": 30775, "slave": 3, "signed": True, "nan_value": 0x80000000},
    "battery_SoC": {"ip": SUNNY_ISLAND_IP, "register": 30845, "slave": 3, "signed": False, "nan_value": 0xFFFFFFFF},
}

ev_charging_modbus_registers = {
    "charging_state": {"ip": CHARGER_ME_IP, "register": 122, "slave": 1},
    "maximum_current": {"ip": CHARGER_ME_IP, "register": 1000, "slave": 1},
}

# Allowed CORS origins hinzufügen
origins = [
    "http://localhost:4200",  # Erlaube die Verbindung von Angular-Frontend
    "http://127.0.0.1:4200",  # Falls du von einer anderen IP aus zugreifst
]

def write_modbus_data(ip: str, register: int, slave: int, value: int):
    try:
        client = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=10)
        connection = client.connect()
        if connection:
            response = client.write_register(register, value, slave=slave)
        else:
            print("Failed to connect to Modbus Server")
        client.close()
    except Exception as e:
        print(f"Error writing to {ip}:{register} - {e}")

def read_modbus_data(ip: str, register: int, slave: int, count: int):
    try:
        client = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=10)
        client.connect()
        response = client.read_holding_registers(register, count=count, slave=slave) # older versions unit instead of slave
        client.close()
        if response and response.registers:
            return response.registers
        else:
            return None  # Return 0 if the register is empty or there is no valid response
    except Exception as e:
        print(f"Error reading {ip}:{register} - {e}")
        return None  # Return 0 in case of an exception

def read_wallbox_modbus_data(ip: str, register: int, slave: int):
    try:
        value = read_modbus_data(ip, register, slave, 1)[0]
        if (value != None):
            return value
        else:
            return 0
    except Exception as e:
        print(f"Error reading {ip}:{register} - {e}")
        return 0  # Return 0 in case of an exception

# Modbus read function
def read_sma_modbus_data(ip: str, register: int, slave: int, signed: bool, nan_value: int):
    try:
        registers = read_modbus_data(ip, register, slave, 2)
        if registers:
            value = combine_registers(registers[0], registers[1])
            if (value == nan_value):
                return 0
            if signed:
                return int.from_bytes(value.to_bytes(length=4), byteorder="big", signed=True)
            else:
                return value
        else:
            return 0  # Return 0 if the register is empty or there is no valid response
    except Exception as e:
        print(f"Error reading {ip}:{register} - {e}")
        return 0  # Return 0 in case of an exception

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

def regulate_ev_charging():
    global ev_charging_state, ev_max_current

    ev_charging_state = read_wallbox_modbus_data(**sma_devices["charging_state"])
    ev_max_current = read_wallbox_modbus_data(**sma_devices["maximum_current"])

    if (ev_charging_state >= 2 and ev_charging_state <= 4):
        calculate_and_set_max_current()

# calculates the maximum current for ev charging and sets a new value if needed
# two phases charging (VW e-Golf)
# battery should not be drained for ev charging
# minimum current 6A - pause current 0A
def calculate_and_set_max_current():
    # calculate if power is drained from the grid and/or battery
    excess_power = (grid_power + battery_power) * (-1)
    if (ev_max_current == 0):
        check_for_charging_start(excess_power)
    else:
        if excess_power > 50 and ev_max_current < 16:
            check_for_power_increase(excess_power)
        elif excess_power < 50:
            check_for_power_decrease(excess_power)

def check_for_charging_start(excess_power: int):
    required_excess_power = 2800 # 2 phases, 6A + buffer
    if (excess_power >= required_excess_power):
        write_modbus_data(**sma_devices["maximum_current"], value=6)

# check if 1A more is possible
def check_for_power_increase(excess_power: int):
    required_excess_power = 480 # 2 phases, 1A + buffer
    if (excess_power >= required_excess_power):
        current = ev_max_current + 1
        write_modbus_data(**sma_devices["maximum_current"], value=current)

# check if 1A less or charging pause is necessary
def check_for_power_decrease(excess_power: int):
    current_charging_power = 2*230*ev_max_current
    one_amp_reduction_power = 2*230 # two phases, 1A

    current_reduction = math.ceil((excess_power * (-1)) / one_amp_reduction_power) # round up always

    new_current = ev_max_current - current_reduction

    if (new_current < 6):
        new_current = 0
    
    write_modbus_data(**sma_devices["maximum_current"], value=new_current)

# Running:
# uvicorn modbus_rest_api:app --host localhost --port 8000 --reload
