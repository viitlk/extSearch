"""Content sources: YouTube, Minecraft mods, freelance boards."""

from __future__ import annotations

import asyncio
from typing import Any

from . import sources as _s
from .errors import ExtSearchError

FREELANCE_BOARDS = tuple(_s.FREELANCE_BOARDS)


async def video_search(query: str, limit: int = 8) -> dict[str, Any]:
    return await _s.youtube_search(query, limit=max(1, min(int(limit), 30)))


async def mod_search(query: str, where: str = "all", limit: int = 10,
                     project_type: str | None = None,
                     sort: str = "relevance") -> dict[str, Any]:
    """Modrinth (API) + CurseForge (HTML); results are interleaved."""
    where = (where or "all").lower()
    limit = max(1, min(int(limit), 30))
    out: dict[str, Any] = {"query": query, "where": where, "sources": {}}

    tasks: list[tuple[str, Any]] = []
    if where in ("all", "*", "modrinth"):
        tasks.append(("modrinth", _s.modrinth_search(
            query, limit=limit, project_type=project_type, sort=sort)))
    if where in ("all", "*", "curseforge"):
        tasks.append(("curseforge", _s.curseforge_search(query, limit=limit)))
    if not tasks:
        raise ExtSearchError("bad_argument", "where must be one of: all, modrinth, curseforge")

    results = await asyncio.gather(*(coro for _, coro in tasks), return_exceptions=True)
    queues: dict[str, list[dict[str, Any]]] = {}
    for (name, _), res in zip(tasks, results):
        if isinstance(res, BaseException):
            out["sources"][name] = {
                "returned": 0,
                "error": getattr(res, "code", type(res).__name__),
            }
            continue
        items = res.get("items") or []
        bucket: list[dict[str, Any]] = []
        for item in items:
            item = dict(item)
            item["source"] = name
            item["id"] = f"{name}:{item.get('id') or item.get('slug')}"
            bucket.append(item)
        queues[name] = bucket
        out["sources"][name] = {"returned": len(items), "total": res.get("total")}

    if not queues:
        raise ExtSearchError("no_results", f"nothing found for {query!r}")

    merged: list[dict[str, Any]] = []
    while any(queues.values()):
        for name in list(queues):
            if queues[name]:
                merged.append(queues[name].pop(0))

    out["returned"] = len(merged[:limit])
    out["items"] = merged[:limit]
    return out


async def freelance_search(query: str, boards: str | list[str] = "all",
                           limit: int = 10) -> dict[str, Any]:
    """Freelance meta-search: site:<board> through DDG/Bing."""
    if isinstance(boards, str):
        if boards.strip().lower() in ("all", "*", ""):
            board_list = list(FREELANCE_BOARDS)
        else:
            board_list = [b.strip().lower() for b in boards.split(",") if b.strip()]
    else:
        board_list = list(boards)
    return await _s.freelance_search(query, boards=board_list,
                                     limit=max(1, min(int(limit), 30)))
