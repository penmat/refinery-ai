"""Minimal OSV batch API client."""

from __future__ import annotations

import json
import math
import ssl
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from refinery_ai.inspectors.dependencies.models import DependencyAnalysisError, Vulnerability
from refinery_ai.logging import get_logger

logger = get_logger(__name__)


class OsvClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int,
        batch_size: int = 1000,
        *,
        verify_ssl: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self.ssl_context = _ssl_context(verify_ssl)

    def query(self, purls: list[str]) -> dict[str, list[Vulnerability]]:
        ids_by_purl: dict[str, set[str]] = {purl: set() for purl in purls}
        modified: dict[str, str] = {}
        for offset in range(0, len(purls), self.batch_size):
            batch = purls[offset : offset + self.batch_size]
            payload = {"queries": [{"package": {"purl": purl}} for purl in batch]}
            response = self._request_json("POST", "/v1/querybatch", payload)
            results = response.get("results")
            if not isinstance(results, list) or len(results) != len(batch):
                raise DependencyAnalysisError("OSV returned an unexpected batch response")
            for purl, result in zip(batch, results, strict=True):
                if not isinstance(result, dict):
                    continue
                for item in result.get("vulns", []):
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        vulnerability_id = item["id"]
                        ids_by_purl[purl].add(vulnerability_id)
                        if isinstance(item.get("modified"), str):
                            modified[vulnerability_id] = item["modified"]
                if result.get("next_page_token"):
                    raise DependencyAnalysisError(
                        "OSV paginated an unusually large vulnerability result; "
                        "narrow the assessment scope"
                    )
        unique_ids = sorted({item for values in ids_by_purl.values() for item in values})
        with ThreadPoolExecutor(max_workers=min(8, len(unique_ids) or 1)) as executor:
            details = dict(
                zip(unique_ids, executor.map(self._vulnerability, unique_ids), strict=True)
            )
        return {
            purl: [
                details.get(item) or Vulnerability(item, modified.get(item), None, "unknown")
                for item in sorted(ids)
            ]
            for purl, ids in ids_by_purl.items()
        }

    def _vulnerability(self, vulnerability_id: str) -> Vulnerability:
        payload = self._request_json("GET", f"/v1/vulns/{quote(vulnerability_id, safe='-._')}")
        return Vulnerability(
            id=vulnerability_id,
            modified=payload.get("modified") if isinstance(payload.get("modified"), str) else None,
            summary=payload.get("summary") if isinstance(payload.get("summary"), str) else None,
            severity=_severity(payload),
        )

    def _request_json(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        logger.info("Calling OSV: %s %s", method, path)
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "User-Agent": "refinery-ai/1"},
        )
        try:
            with urlopen(
                request, timeout=self.timeout_seconds, context=self.ssl_context
            ) as response:
                decoded = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise DependencyAnalysisError(f"OSV request failed: {error}") from error
        if not isinstance(decoded, dict):
            raise DependencyAnalysisError("OSV returned an unexpected response")
        return decoded


def _severity(payload: dict[str, Any]) -> str:
    database = payload.get("database_specific")
    if isinstance(database, dict):
        value = database.get("severity")
        if isinstance(value, str) and value.casefold() in {
            "low",
            "moderate",
            "medium",
            "high",
            "critical",
        }:
            return "medium" if value.casefold() == "moderate" else value.casefold()
    scores = payload.get("severity", [])
    for entry in scores if isinstance(scores, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("score"), str):
            score = _cvss3(entry["score"])
            if score is not None:
                return (
                    "critical"
                    if score >= 9
                    else "high"
                    if score >= 7
                    else "medium"
                    if score >= 4
                    else "low"
                )
    return "unknown"


def _cvss3(vector: str) -> float | None:
    if not vector.startswith("CVSS:3."):
        return None
    values = dict(part.split(":", 1) for part in vector.split("/")[1:] if ":" in part)
    try:
        av = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[values["AV"]]
        ac = {"L": 0.77, "H": 0.44}[values["AC"]]
        scope = values["S"]
        pr = (
            {"N": 0.85, "L": 0.62, "H": 0.27} if scope == "U" else {"N": 0.85, "L": 0.68, "H": 0.5}
        )[values["PR"]]
        ui = {"N": 0.85, "R": 0.62}[values["UI"]]
        impact_values = {"H": 0.56, "L": 0.22, "N": 0.0}
        isc = 1 - (1 - impact_values[values["C"]]) * (1 - impact_values[values["I"]]) * (
            1 - impact_values[values["A"]]
        )
    except KeyError:
        return None
    impact = 6.42 * isc if scope == "U" else 7.52 * (isc - 0.029) - 3.25 * (isc - 0.02) ** 15
    if impact <= 0:
        return 0.0
    base = impact + 8.22 * av * ac * pr * ui
    base = min(base if scope == "U" else 1.08 * base, 10)
    return math.ceil(base * 10 - 1e-10) / 10


def _ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if verify_ssl:
        try:
            import certifi
        except ImportError:
            return ssl.create_default_context()
        return ssl.create_default_context(cafile=certifi.where())
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context
