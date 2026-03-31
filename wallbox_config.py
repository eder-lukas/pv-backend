CHARGER_ME_IP = "192.168.188.94"
KEBA_WALLBOX_IP = "192.168.188.x"

WALLBOX_CONFIGS = [
    {
        "id": 1,
        "name": "Halle (Lukas)",

        "charging_state": {
            "ip": CHARGER_ME_IP,
            "register": 122,
            "slave": 1,
        },
        "maximum_current": {
            "ip": CHARGER_ME_IP,
            "register": 1000,
            "slave": 1,
        },
    },
    {
        "id": 2,
        "name": "Garage (Papa)",

        "charging_state": {
            "ip": KEBA_WALLBOX_IP,
            "register": 122,
            "slave": 1,
        },
        "maximum_current": {
            "ip": KEBA_WALLBOX_IP,
            "register": 1000,
            "slave": 1,
        },
    },
]