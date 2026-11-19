"""Process cleanup for Git, OCR, and CLI providers."""

import asyncio
import os
import signal
from contextlib import suppress


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a subprocess started in a new POSIX session and reap its leader."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
    elif process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    if process.returncode is None:
        await process.wait()
