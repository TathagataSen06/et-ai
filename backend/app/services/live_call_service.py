"""Live call ingestion: score a scam session while it is still in progress.

A telecom provider (or a victim-side app) streams transcript chunks as the call
unfolds. Every chunk re-scores the accumulated transcript, so the session is
flagged the moment the script crosses into scam territory — which is the point
of the exercise: the alert has to land *before* the transfer, not after the
complaint.

Live buffers are held in memory (a call is short-lived and a restart losing an
in-flight call is acceptable); a durable ScamSession row is written the first
time a call escalates, and again when it is finalised.
"""
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services.scam_detection_service import ScamAssessment, ScamDetector

logger = logging.getLogger(__name__)

CALL_TTL = timedelta(hours=6)      # abandoned buffers are reclaimed after this
MAX_TRANSCRIPT = 40_000


@dataclass
class LiveCall:
    call_id: str
    caller_number: str | None
    victim_contact: str | None
    channel: str
    device_hash: str | None
    chunks: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    alerted: bool = False
    alerted_at_chunk: int | None = None
    session_id: str | None = None

    @property
    def transcript(self) -> str:
        return " ".join(self.chunks)[:MAX_TRANSCRIPT]

    @property
    def duration_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds() / 60.0


class LiveCallTracker:
    """In-memory registry of in-flight calls. Thread-safe."""

    def __init__(self) -> None:
        self._calls: dict[str, LiveCall] = {}
        self._lock = threading.Lock()
        self._detector = ScamDetector()

    def _reap(self) -> None:
        cutoff = datetime.now(timezone.utc) - CALL_TTL
        stale = [k for k, c in self._calls.items() if c.updated_at < cutoff]
        for k in stale:
            self._calls.pop(k, None)

    def get(self, call_id: str) -> LiveCall | None:
        with self._lock:
            return self._calls.get(call_id)

    def ingest(self, call_id: str, chunk: str, *, caller_number: str | None = None,
               victim_contact: str | None = None, channel: str = "VOICE",
               device_hash: str | None = None) -> tuple[LiveCall, ScamAssessment]:
        """Append a chunk and re-score the accumulated transcript."""
        with self._lock:
            self._reap()
            call = self._calls.get(call_id)
            if call is None:
                call = LiveCall(
                    call_id=call_id,
                    caller_number=caller_number,
                    victim_contact=victim_contact,
                    channel=(channel or "VOICE").upper(),
                    device_hash=device_hash,
                )
                self._calls[call_id] = call
            else:
                # Later chunks may carry metadata the first one lacked.
                call.caller_number = call.caller_number or caller_number
                call.victim_contact = call.victim_contact or victim_contact
                call.device_hash = call.device_hash or device_hash
            if chunk:
                call.chunks.append(chunk.strip())
            call.updated_at = datetime.now(timezone.utc)

        assessment = self._detector.assess(
            call.transcript,
            caller_number=call.caller_number,
            channel=call.channel,
            duration_minutes=call.duration_minutes,
        )
        return call, assessment

    def close(self, call_id: str) -> LiveCall | None:
        with self._lock:
            return self._calls.pop(call_id, None)

    def active(self) -> list[LiveCall]:
        with self._lock:
            self._reap()
            return sorted(self._calls.values(), key=lambda c: c.updated_at, reverse=True)


tracker = LiveCallTracker()
