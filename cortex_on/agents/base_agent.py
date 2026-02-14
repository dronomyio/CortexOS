"""
Base Agent — CortexON pattern.
"""
import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentResult:
    agent_name: str
    status: AgentStatus
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name
        self.status = AgentStatus.IDLE
        self.logger = logging.getLogger(f"agent.{name}")

    @abstractmethod
    async def execute(self, task: Dict[str, Any]) -> AgentResult:
        ...

    async def run_with_timeout(self, task: Dict, timeout: float = 120.0) -> AgentResult:
        self.status = AgentStatus.RUNNING
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(self.execute(task), timeout=timeout)
            self.status = AgentStatus.COMPLETED
            result.duration_ms = (time.perf_counter() - start) * 1000
            return result
        except asyncio.TimeoutError:
            self.status = AgentStatus.FAILED
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Timed out after {timeout}s",
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as e:
            self.status = AgentStatus.FAILED
            self.logger.exception(f"Agent failed: {e}")
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=str(e),
                duration_ms=(time.perf_counter() - start) * 1000,
            )
