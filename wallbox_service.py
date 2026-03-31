from wallbox_config import WALLBOX_CONFIGS
from wallbox_state import wallbox_states

def get_wallbox_config(wallbox_id: int):
    return next(wb for wb in WALLBOX_CONFIGS if wb["id"] == wallbox_id)

def get_wallbox_state(wallbox_id: int):
    return wallbox_states[wallbox_id]

def update_wallbox_state(wallbox_id: int, key: str, value):
    wallbox_states[wallbox_id][key] = value