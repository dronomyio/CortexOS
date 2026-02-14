import asyncio
from typing import Any, Callable
from functools import wraps
from fastapi import HTTPException

def timeout(seconds: int) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                raise HTTPException(
                    status_code=500,
                    detail="Task exceeded the maximum allowed time."
                )
        return wrapper
    return decorator