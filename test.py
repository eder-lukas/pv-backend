
from pymodbus.client import ModbusTcpClient # older versions pymodbus.client.sync

MODBUS_PORT = 502

# Modbus read function
def read_modbus_data(ip: str, register: int, slave: int):
    try:
        client = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=10)
        client.connect()
        response = client.read_holding_registers(register, count=1, slave=slave) # older versions unit instead of slave
        print(response)
        client.close()
        if response and response.registers:
            return response.registers[0]
        else:
            return 0  # Return 0 if the register is empty or there is no valid response
    except Exception as e:
        print(f"Error reading {ip}:{register} - {e}")
        return 0  # Return 0 in case of an exception


def write_modbus_data(ip: str, register: int, slave: int, value: int):
    try:
        client = ModbusTcpClient(ip, port=MODBUS_PORT, timeout=10)
        connection = client.connect()

        if connection:
            response = client.write_register(register, value, slave=slave)
            print(response)
        else:
            print("Failed to connect to Modbus Server")

        client.close()

    except Exception as e:
        print(f"Error writing to {ip}:{register} - {e}")



value = read_modbus_data("192.168.188.94", 1000, 1)
print(value)
write_modbus_data("192.168.188.94", 1000, 1, 6)
value = read_modbus_data("192.168.188.94", 1000, 1)
print(value)




# juice charger me mit slave id 1 und ip 192.168.188.94
# lese register 122 1 byte für cp connection state (1-A: EV disconnected; 2-B: EV connected; 3-C: EV charge; 4-D: EV charge (ventilation required); 5-E: Error condition; 6-F: Fault condition)
# lese register 706 1 byte für signaled_current (maxumum current for ev charging)
# lese/schreibe Register 1000 für max-current regelung