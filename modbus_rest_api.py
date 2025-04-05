from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from pymodbus.client import ModbusTcpClient # older versions pymodbus.client.sync
import socket
import struct
import asyncio
from contextlib import asynccontextmanager

UDP_IP = "192.168.188.39"
UDP_PORT = 9522

TRIPOWER_IP = "192.168.188.45"
SUNNY_ISLAND_IP = "192.168.188.117"
MODBUS_PORT = 502

app = FastAPI()

# Global variables for UDP data
grid_power = 0 # negative == feed_in, positive == consumption
emeter_power = 0 # positive == production

# values are in two registers
def combine_registers(high, low):
    return (high << 16) + low

# Device configuration
devices = {
    "tripower_total_power": {"ip": TRIPOWER_IP, "register": 30775, "slave": 3, "signed": False, "NaN-Value": 0x80000000},
    "tripower_str1_power": {"ip": TRIPOWER_IP, "register": 30773, "slave": 3, "signed": False, "NaN-Value": 0x80000000},
    "tripower_str2_power": {"ip": TRIPOWER_IP, "register": 30961, "slave": 3, "signed": False, "NaN-Value": 0x80000000},
    "tripower_str3_power": {"ip": TRIPOWER_IP, "register": 30967, "slave": 3, "signed": False, "NaN-Value": 0x80000000},

    "battery_power": {"ip": SUNNY_ISLAND_IP, "register": 30775, "slave": 3, "signed": True, "NaN-Value": 0x80000000},
    "battery_SoC": {"ip": SUNNY_ISLAND_IP, "register": 30845, "slave": 3, "signed": False, "NaN-Value": 0xFFFFFFFF},
}

# Allowed CORS origins hinzufügen
origins = [
    "http://localhost:4200",  # Erlaube die Verbindung von Angular-Frontend
    "http://127.0.0.1:4200",  # Falls du von einer anderen IP aus zugreifst
]

# Modbus read function
def read_modbus_data(ip: str, register: int, slave: int, signed: bool, nan_value: int):
    try:
        client = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=10)
        client.connect()
        response = client.read_holding_registers(register, count=2, slave=slave) # older versions unit instead of slave
        client.close()
        if response and response.registers:
            value = combine_registers(response.registers[0], response.registers[1])
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
    udp_task = asyncio.create_task(udp_listener())  # Start UDP listener
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
    data["tripower_power"] = read_modbus_data(**devices["tripower_total_power"])
    data["tripower_str1_power"] = read_modbus_data(**devices["tripower_str1_power"])
    data["tripower_str2_power"] = read_modbus_data(**devices["tripower_str2_power"])
    data["tripower_str3_power"] = read_modbus_data(**devices["tripower_str3_power"])
    
    # Read battery data
    data["battery_power"] = read_modbus_data(**devices["battery_power"])
    data["battery_SoC"] = read_modbus_data(**devices["battery_SoC"])
    
    data["grid_power"] = round(grid_power / 10)
    data["emeter_power"] = round(emeter_power / 10)

    # Calculate house power
    data["consumption"] = (
        (data["tripower_power"] or 0) + (data["emeter_power"] or 0)
        + (data["grid_power"] or 0) + (data["battery_power"] or 0)
    )
    
    return data

# Async UDP listener
async def udp_listener():
    global grid_power, emeter_power
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    sock.setblocking(False)  # Make socket non-blocking

    print(f"✅ UDP server running on Port {UDP_PORT}...")

    loop = asyncio.get_running_loop()

    while True:
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


# Running:
# uvicorn modbus_rest_api:app --host localhost --port 8000 --reload
