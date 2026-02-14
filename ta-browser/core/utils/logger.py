#import logfire

import os
import logging

try:
    import logfire
except Exception:
    logfire = None


from typing import Optional, Any
from contextvars import ContextVar

# Create a context variable to store request-specific job IDs
_job_id_ctx = ContextVar('job_id', default=None)

class Logger:
    _instance: Optional['Logger'] = None
    _base_logger = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__11(self, environment='dev', scrubbing=False):
        """
        Initialize the logger with configuration
        
        Args:
            environment (str): Environment name (default: 'dev')
            scrubbing (bool): Enable/disable log scrubbing (default: False)
        """
        if self._base_logger is None:
            self._base_logger = logfire.configure(
                environment=environment,
                scrubbing=scrubbing,
            )

    def __init__(self):
        token = os.getenv("LOGFIRE_TOKEN", "").strip()
        send = os.getenv("LOGFIRE_SEND_TO_LOGFIRE", "true").lower() in ("1", "true", "yes")

        # If no token (common in local docker) or send disabled, do standard logging.
        if not token or not send or logfire is None:
            self._base_logger = logging.getLogger("agentic_browser")
            self._base_logger.setLevel(logging.INFO)
            return

        # Only configure Logfire when token exists
        self._base_logger = logfire.configure(token=token)

    def _get_contextual_logger(self):
        """Get logger instance with current job_id context"""
        job_id = _job_id_ctx.get()
        if job_id:
            return self._base_logger.with_tags(str(job_id))
        return self._base_logger

    def trace(self, message: str, **kwargs: Any) -> None:
        """Log trace level message"""
        self._get_contextual_logger().trace(message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug level message"""
        self._get_contextual_logger().debug(message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info level message"""
        self._get_contextual_logger().info(message, **kwargs)

    def notice(self, message: str, **kwargs: Any) -> None:
        """Log notice level message"""
        self._get_contextual_logger().notice(message, **kwargs)
    
    def warn(self, message: str, **kwargs: Any) -> None:
        """Alias for warning"""
        self._get_contextual_logger().warn(message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error level message"""
        self._get_contextual_logger().error(message, **kwargs)

    def fatal(self, message: str, **kwargs: Any) -> None:
        """Log fatal level message"""
        self._get_contextual_logger().fatal(message, **kwargs)


    def set_job_id(self, job_id):
        """
        Sets job_id as a tag
        
        Args:
            job_id: The job identifier to tag logs with
            
        Returns:
            Logger instance with job_id tag
        """
        if job_id:
            _job_id_ctx.set(str(job_id))
        return self._get_contextual_logger()
