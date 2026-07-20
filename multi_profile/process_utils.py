from __future__ import annotations

import os
import signal
from collections.abc import Callable
from typing import Any


def terminate_process_tree(
    process: Any,
    *,
    getpgid: Callable[[int], int] = os.getpgid,
    killpg: Callable[[int, int], None] = os.killpg,
) -> None:
    try:
        killpg(getpgid(process.pid), signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait()
    except Exception:
        pass
