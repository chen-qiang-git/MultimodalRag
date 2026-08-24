"""Compatibility API for the Android client's retired memory screen.

Long-term user data is now stored as user preference entries.  These endpoints
retain the old response shape while exposing that canonical storage.
"""

from fastapi import APIRouter, Query

from app.repositories.user_preference_repo import get_user_preference_repo


router = APIRouter()


def _memory_payload(entry: dict) -> dict:
    return {
        "memory_id": entry.get("entry_id", ""),
        "user_id": entry.get("user_id", ""),
        "memory_type": entry.get("category") or "preference",
        "content": entry.get("raw_text", ""),
        "structured_value": entry,
        "source": "user_preference_entry",
        "confidence": 1.0,
        "status": "active" if entry.get("enabled", True) else "disabled",
        "session_id": "",
        "conversation_id": "",
        "decay_weight": 1.0,
        "created_at": entry.get("created_at", ""),
    }


@router.get("/api/memories")
async def list_memories(user_id: str = Query(default="")):
    entries = await get_user_preference_repo().alist_all(user_id)
    memories = [_memory_payload(entry.to_dict()) for entry in entries]
    return {"user_id": user_id, "count": len(memories), "memories": memories}


@router.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, user_id: str = Query(default="")):
    deleted = await get_user_preference_repo().adelete(memory_id, user_id)
    return {"status": "ok" if deleted else "not_found", "memory_id": memory_id}
