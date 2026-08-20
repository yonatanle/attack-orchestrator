from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Union

from .errors import DeviceDisconnectedError, OrchestratorError
from .reconnect import try_reconnect
from .transport.base import DeviceClient

# What a single file/directory's read or listing is allowed to fail with
# without sinking the rest of the walk: device/protocol errors the
# transport raises (OrchestratorError and its subclasses, e.g.
# DeviceNotUnlockedError), a missing remote path (FileNotFoundError), and
# local filesystem errors writing the mirrored copy (OSError). Anything
# else -- TypeError, AttributeError, etc. -- is a framework bug and should
# propagate instead of being silently recorded as a per-file error.
_PER_ENTRY_ERRORS = (OrchestratorError, FileNotFoundError, OSError)

logger = logging.getLogger(__name__)


@dataclass
class ExtractionReport:
    files_written: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return not self.errors


class ExtractionSession:
    """
    Capability object handed back once an attack chain completes -- nothing
    here re-runs stages, it just wraps the now-unlocked DeviceClient.

    A dropped connection during extraction gets the same bounded
    reconnect-and-retry treatment AttackOrchestrator gives a dropped
    connection during stage execution: extraction can legitimately take a
    while walking a large filesystem, and there's no reason a network
    hiccup there should be handled worse than one during the attack itself.
    `max_reconnect_attempts` defaults independently of any orchestrator that
    created this session, since ExtractionSession is usable standalone.
    """

    def __init__(self, device_client: DeviceClient, max_reconnect_attempts: int = 1):
        self._client = device_client
        self._max_reconnect_attempts = max_reconnect_attempts

    def read_file(self, path: str) -> bytes:
        return self._with_reconnect(lambda: self._client.read_file(path))

    def extract_all(self, dest_dir: Union[str, Path], root: str = "/") -> ExtractionReport:
        """
        Walks the device's filesystem from `root` via list_dir and mirrors it
        under dest_dir, downloading each file via read_file. A single file's
        (or subdirectory's) error is recorded in the report and does not
        abort the rest of the walk -- one unreadable file shouldn't sink an
        otherwise-successful extraction.
        """
        dest_dir = Path(dest_dir)
        report = ExtractionReport()
        self._walk(root, dest_dir, report)
        logger.info(
            "extraction: %d file(s) written to %s%s",
            len(report.files_written),
            dest_dir,
            f", {len(report.errors)} error(s)" if report.errors else "",
        )
        return report

    def _walk(self, remote_dir: str, local_dir: Path, report: ExtractionReport) -> None:
        try:
            entries = self._with_reconnect(lambda: self._client.list_dir(remote_dir))
        except _PER_ENTRY_ERRORS as exc:
            report.errors[remote_dir] = str(exc)
            return

        local_dir.mkdir(parents=True, exist_ok=True)
        for name, is_dir in entries:
            remote_path = f"{remote_dir.rstrip('/')}/{name}"
            if is_dir:
                self._walk(remote_path, local_dir / name, report)
                continue
            try:
                data = self._with_reconnect(lambda: self._client.read_file(remote_path))
            except _PER_ENTRY_ERRORS as exc:
                report.errors[remote_path] = str(exc)
                continue
            (local_dir / name).write_bytes(data)
            report.files_written.append(remote_path)

    def _with_reconnect(self, operation):
        """Runs a zero-arg callable; on a dropped connection, makes one bounded
        reconnect attempt and retries the operation once before giving up."""
        try:
            return operation()
        except DeviceDisconnectedError:
            if not try_reconnect(self._client, self._max_reconnect_attempts):
                raise
            return operation()
