
from pymodbus.client import ModbusTcpClient # older versions pymodbus.client.sync

MODBUS_PORT = 502

# values are in two registers
def combine_registers(high, low):
    print(high << 16)
    print(low)
    return (high << 16) + low

# Modbus read function
def read_modbus_data(ip: str, register: int, slave: int, signed: bool):
    try:
        client = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=10)
        client.connect()
        response = client.read_holding_registers(register, count=2, slave=slave) # older versions unit instead of slave
        client.close()
        if response and response.registers:
            value = combine_registers(32768, 0)
            # value = combine_registers(response.registers[0], response.registers[1])
            if signed:
                return int.from_bytes(value.to_bytes(length=4), byteorder="big", signed=True)
            else:
                return value
        else:
            return 0  # Return 0 if the register is empty or there is no valid response
    except Exception as e:
        print(f"Error reading {ip}:{register} - {e}")
        return 0  # Return 0 in case of an exception


value = read_modbus_data("192.168.188.45", 30775, 3, True)
print(bin(value))
print(value)
print(0x8000)
# value = combine_registers(b'\F0\00', b'\00\00')
# print(int.from_bytes(value.to_bytes(length=4), byteorder="big", signed=True))
# high = int.from_bytes(b'\F0\00', byteorder='big', signed=True)
# low = int.from_bytes(b'\00\00')
# print(bin(high))
# 1000 0000 0000 0000 0000 0000 0000