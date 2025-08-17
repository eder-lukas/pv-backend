from pymodbus.client import ModbusTcpClient # older versions pymodbus.client.sync


TRIPOWER_IP = "192.168.188.45"
SUNNY_ISLAND_IP = "192.168.188.117"
CHARGER_ME_IP = "192.168.188.94"
MODBUS_PORT = 502


# values are in two registers
def combine_registers(high, low):
    return (high << 16) + low


# Device/register configuration
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