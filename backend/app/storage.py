from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from app.models import CalibrationSession, SessionSummary, session_path


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[SessionSummary]:
        sessions: list[CalibrationSession] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                sessions.append(self._read_path(path))
            except (OSError, ValueError):
                continue
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return [SessionSummary.from_session(session) for session in sessions]

    def create(self, session: CalibrationSession) -> CalibrationSession:
        self.save(session)
        return session

    def get(self, session_id: str) -> CalibrationSession:
        path = session_path(self.root, session_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Session not found")
        return self._read_path(path)

    def save(self, session: CalibrationSession) -> CalibrationSession:
        path = session_path(self.root, session.id)
        payload = session.model_dump(mode="json")
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return session

    def _read_path(self, path: Path) -> CalibrationSession:
        return CalibrationSession.model_validate_json(path.read_text(encoding="utf-8"))

