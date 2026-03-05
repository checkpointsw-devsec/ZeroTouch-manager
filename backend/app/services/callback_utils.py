"""
Shared async callback helper used by all deployment orchestrators.
"""
import inspect
from typing import Callable, Optional
from loguru import logger


async def call_callback(callback: Optional[Callable], *args, **kwargs):
    """Call a callback that may be sync or async, swallowing exceptions.

    Args:
        callback: The callback function to invoke (may be ``None``).
        *args, **kwargs: Arguments forwarded to the callback.
    """
    if callback is None:
        return
    try:
        result = callback(*args, **kwargs)
        if inspect.isawaitable(result):
            await result
    except Exception as e:
        logger.warning(f"Callback failed: {e}")
