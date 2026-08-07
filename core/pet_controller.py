"""Command channel shared with the standalone desktop pet."""
from __future__ import annotations

import json
from pathlib import Path
import time

from core.storage import APP_DIR


COMMAND_FILE = APP_DIR / "pet_command.json"


def send_pet_command(**command) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    command["timestamp"] = time.time()
    COMMAND_FILE.write_text(json.dumps(command), encoding="utf-8")
