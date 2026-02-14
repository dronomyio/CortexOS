"""
Parallel.ai Client — Search, Extract, Task APIs.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from config import ParallelAIConfig

logger = logging.getLogger(__name__)


class ParallelClient:
    def __init__(self, config: ParallelAIConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "Content-Type": "application/json",
                "x-api-key": self.config.api_key,
                "parallel-beta": self.config.beta_header,
            })
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def search(
        self, objective: str, search_queries: List[str],
        max_results: int = 10, max_chars_per_result: int = 10000,
    ) -> Dict[str, Any]:
        session = await self._get_session()
        payload = {
            "objective": objective,
            "search_queries": search_queries,
            "max_results": max_results,
            "excerpts": {"max_chars_per_result": max_chars_per_result},
        }
        try:
            async with session.post(self.config.search_endpoint, json=payload) as r:
                if r.status == 200:
                    return await r.json()
                return {"error": await r.text(), "status": r.status}
        except Exception as e:
            return {"error": str(e)}

    async def extract(
        self, urls: List[str], objective: str,
        excerpts: bool = True, full_content: bool = False,
    ) -> Dict[str, Any]:
        session = await self._get_session()
        payload = {"urls": urls, "objective": objective,
                   "excerpts": excerpts, "full_content": full_content}
        try:
            async with session.post(self.config.extract_endpoint, json=payload) as r:
                if r.status == 200:
                    return await r.json()
                return {"error": await r.text(), "status": r.status}
        except Exception as e:
            return {"error": str(e)}

    async def task(self, query: str, processor: str = "base") -> Dict[str, Any]:
        session = await self._get_session()
        payload = {"query": query, "processor": processor}
        try:
            async with session.post(self.config.task_endpoint, json=payload) as r:
                if r.status == 200:
                    return await r.json()
                return {"error": await r.text(), "status": r.status}
        except Exception as e:
            return {"error": str(e)}

    async def batch_search(
        self, queries: List[Dict], max_concurrent: int = 5,
    ) -> List[Dict]:
        sem = asyncio.Semaphore(max_concurrent)
        async def _bounded(q):
            async with sem:
                return await self.search(
                    objective=q["objective"],
                    search_queries=q.get("search_queries", []),
                    max_results=q.get("max_results", 5),
                )
        return await asyncio.gather(*[_bounded(q) for q in queries], return_exceptions=True)
