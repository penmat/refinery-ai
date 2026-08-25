"""Small authenticated client for a temporary local SonarQube server."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from refinery_ai.inspectors.sonarqube.models import SonarQubeError, SonarQubeTimeout
from refinery_ai.logging import get_logger

logger = get_logger(__name__)

OpenUrl = Callable[..., Any]


class SonarQubeClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        request_timeout: int = 15,
        open_url: OpenUrl = urllib.request.urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.request_timeout = request_timeout
        self.open_url = open_url

    def wait_ready(
        self,
        timeout: int,
        poll_interval: float,
        *,
        container_running: Callable[[], bool] | None = None,
    ) -> None:
        deadline = time.monotonic() + timeout
        last_status = "not reachable"
        while time.monotonic() < deadline:
            if container_running is not None and not container_running():
                raise SonarQubeError("SonarQube container exited during startup")
            try:
                response = self.get_json("/api/system/status")
                last_status = str(response.get("status", "unknown"))
                if last_status == "UP":
                    return
            except SonarQubeError as error:
                last_status = str(error)
            time.sleep(poll_interval)
        raise SonarQubeTimeout(f"SonarQube startup timed out; last status: {last_status}")

    def change_admin_password(self, new_password: str) -> None:
        self._request(
            "POST",
            "/api/users/change_password",
            fields={"login": "admin", "previousPassword": "admin", "password": new_password},
            basic_credentials=("admin", "admin"),
        )

    def generate_token(self, name: str, admin_password: str) -> str:
        response = self.post_form(
            "/api/user_tokens/generate",
            {"name": name},
            basic_credentials=("admin", admin_password),
        )
        token = response.get("token")
        if not isinstance(token, str) or not token:
            raise SonarQubeError("SonarQube did not return an analysis token")
        return token

    def server_version(self) -> str:
        return self.get_text("/api/server/version").strip()

    def wait_for_task(self, task_id: str, timeout: int, poll_interval: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.get_json("/api/ce/task", {"id": task_id}).get("task", {})
            status = task.get("status") if isinstance(task, dict) else None
            if status == "SUCCESS":
                analysis_id = task.get("analysisId")
                return str(analysis_id) if analysis_id else ""
            if status in {"FAILED", "CANCELED"}:
                raise SonarQubeError(f"SonarQube compute task ended with status {status}")
            time.sleep(poll_interval)
        raise SonarQubeTimeout("SonarQube compute task timed out")

    def measures(self, project_key: str, metric_keys: list[str]) -> dict[str, Any]:
        component = self.get_json(
            "/api/measures/component",
            {"component": project_key, "metricKeys": ",".join(metric_keys)},
        ).get("component", {})
        values: dict[str, Any] = {}
        if isinstance(component, dict):
            for measure in component.get("measures", []):
                if isinstance(measure, dict) and isinstance(measure.get("metric"), str):
                    values[measure["metric"]] = measure.get("value")
        return values

    def issues(self, project_key: str) -> list[dict[str, Any]]:
        page = 1
        issues: list[dict[str, Any]] = []
        while True:
            response = self.get_json(
                "/api/issues/search",
                {"componentKeys": project_key, "resolved": "false", "ps": "500", "p": str(page)},
            )
            batch = response.get("issues", [])
            if not isinstance(batch, list):
                raise SonarQubeError("SonarQube returned an invalid issues response")
            issues.extend(item for item in batch if isinstance(item, dict))
            paging = response.get("paging", {})
            total = paging.get("total", len(issues)) if isinstance(paging, dict) else len(issues)
            if len(issues) >= int(total) or not batch:
                return issues
            page += 1

    def quality_profiles(self, project_key: str) -> dict[str, str]:
        response = self.get_json("/api/qualityprofiles/search", {"project": project_key})
        profiles: dict[str, str] = {}
        for profile in response.get("profiles", []):
            if isinstance(profile, dict) and profile.get("language") and profile.get("name"):
                profiles[str(profile["language"])] = str(profile["name"])
        return profiles

    def get_json(self, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        text = self._request("GET", path, query=query)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise SonarQubeError("SonarQube returned invalid JSON") from error
        if not isinstance(value, dict):
            raise SonarQubeError("SonarQube returned an unexpected response")
        return value

    def get_text(self, path: str) -> str:
        return self._request("GET", path)

    def post_form(
        self,
        path: str,
        fields: dict[str, str],
        *,
        basic_credentials: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        text = self._request("POST", path, fields=fields, basic_credentials=basic_credentials)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise SonarQubeError("SonarQube returned invalid JSON") from error
        if not isinstance(value, dict):
            raise SonarQubeError("SonarQube returned an unexpected response")
        return value

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        fields: dict[str, str] | None = None,
        basic_credentials: tuple[str, str] | None = None,
    ) -> str:
        logger.info("Calling SonarQube: %s %s", method, path)
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = urllib.parse.urlencode(fields).encode() if fields is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if basic_credentials:
            encoded = base64.b64encode(f"{basic_credentials[0]}:{basic_credentials[1]}".encode())
            request.add_header("Authorization", f"Basic {encoded.decode()}")
        elif self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        if fields is not None:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.open_url(request, timeout=self.request_timeout) as response:
                return bytes(response.read()).decode()
        except urllib.error.HTTPError as error:
            raise SonarQubeError(
                f"SonarQube request failed with HTTP {error.code} at {path}"
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SonarQubeError(f"SonarQube request failed: {type(error).__name__}") from error
