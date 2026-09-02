"""Shared OpenTelemetry configuration for Pydantic AI and LiteLLM."""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import Any
from urllib.parse import unquote

from refinery_ai.config import Settings

_lock = Lock()
_configured = False
_litellm_callbacks: tuple[Any, ...] = ()
_token_counter: Any = None
_request_counter: Any = None
_observation: ContextVar[dict[str, str | int] | None] = ContextVar(
    "refinery_llm_observation", default=None
)


def _signal_endpoint(base: str, signal: str) -> str:
    endpoint = base.rstrip("/")
    for existing in ("traces", "metrics", "logs"):
        suffix = f"/v1/{existing}"
        if endpoint.endswith(suffix):
            return endpoint.removesuffix(suffix) + f"/v1/{signal}"
    return endpoint + f"/v1/{signal}"


def _headers(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    result: dict[str, str] = {}
    for item in value.split(","):
        name, separator, header_value = item.partition("=")
        if separator and name.strip():
            result[name.strip()] = unquote(header_value.strip())
    return result


def configure_telemetry(settings: Settings | None = None) -> bool:
    """Configure OTLP export once and instrument both supported LLM clients.

    Returns whether telemetry is active. Calling this repeatedly is safe.
    """

    global _configured, _litellm_callbacks, _request_counter, _token_counter

    config = settings or Settings()
    if not config.otel_enabled:
        return False

    with _lock:
        if _configured:
            return True

        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from pydantic_ai import Agent, InstrumentationSettings

        headers = _headers(
            config.otel_headers.get_secret_value() if config.otel_headers is not None else None
        )
        resource = Resource.create({"service.name": config.otel_service_name})

        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=_signal_endpoint(config.otel_endpoint, "traces"),
                    headers=headers,
                )
            )
        )
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=_signal_endpoint(config.otel_endpoint, "metrics"),
                        headers=headers,
                    )
                )
            ],
        )
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        meter = meter_provider.get_meter("refinery-ai")
        _token_counter = meter.create_counter(
            "refinery.llm.tokens",
            unit="{token}",
            description="LLM tokens attributed to a Refinery workflow phase",
        )
        _request_counter = meter.create_counter(
            "refinery.llm.requests",
            unit="{request}",
            description="LLM model requests attributed to a Refinery workflow phase",
        )

        instrumentation = InstrumentationSettings(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
            include_content=config.otel_include_content,
            include_binary_content=config.otel_include_content,
        )
        Agent.instrument_all(instrumentation)

        from litellm.integrations.opentelemetry import OpenTelemetry, OpenTelemetryConfig

        class ClientOpenTelemetry(OpenTelemetry):
            """LiteLLM telemetry without its optional proxy-server registration."""

            def _init_otel_logger_on_litellm_proxy(self) -> None:
                return None

        callback = ClientOpenTelemetry(
            config=OpenTelemetryConfig(
                service_name=config.otel_service_name,
                enable_metrics=True,
            ),
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )
        _litellm_callbacks = (callback,)
        _configured = True
        return True


def litellm_callbacks() -> tuple[Any, ...]:
    """Return the configured LiteLLM callbacks, or no callbacks when disabled."""

    return _litellm_callbacks


@contextmanager
def observe_llm(
    phase: str,
    *,
    run_id: str,
    plan_id: str | None = None,
    repair_attempt: int | None = None,
) -> Iterator[None]:
    """Attach stable Refinery workflow dimensions to spans and usage metrics."""

    attributes: dict[str, str | int] = {
        "refinery.phase": phase,
        "refinery.run.id": run_id,
    }
    if plan_id is not None:
        attributes["refinery.plan.id"] = plan_id
    if repair_attempt is not None:
        attributes["refinery.repair.attempt"] = repair_attempt

    token = _observation.set(attributes)
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("refinery-ai")
        with tracer.start_as_current_span(f"refinery.{phase}", attributes=attributes):
            yield
    finally:
        _observation.reset(token)


def record_llm_usage(usage: Any, *, model: str | None = None) -> None:
    """Record token and request usage using the active Refinery workflow dimensions."""

    if _token_counter is None or _request_counter is None or usage is None:
        return

    def value(name: str, fallback: str | None = None) -> int:
        if isinstance(usage, Mapping):
            raw = usage.get(name, usage.get(fallback, 0) if fallback else 0)
        else:
            raw = getattr(usage, name, getattr(usage, fallback, 0) if fallback else 0)
        return int(raw or 0)

    attributes: dict[str, str | int] = dict(_observation.get() or {})
    if model:
        attributes["gen_ai.request.model"] = model
    for token_type, amount in (
        ("input", value("input_tokens", "prompt_tokens")),
        ("output", value("output_tokens", "completion_tokens")),
        ("cache_read", value("cache_read_tokens")),
        ("cache_write", value("cache_write_tokens")),
    ):
        if amount:
            _token_counter.add(amount, {**attributes, "gen_ai.token.type": token_type})
    _request_counter.add(value("requests") or 1, attributes)


def run_observed_agent(
    agent: Any,
    prompt: str,
    *,
    deps: Any,
    phase: str,
    run_id: str,
    plan_id: str | None = None,
    model: str | None = None,
) -> Any:
    """Run a synchronous Pydantic AI agent and attribute its aggregate usage."""

    with observe_llm(phase, run_id=run_id, plan_id=plan_id):
        parameters = inspect.signature(agent.run_sync).parameters
        kwargs: dict[str, Any] = {"deps": deps}
        if "run_id" in parameters:
            kwargs["run_id"] = run_id
        if "metadata" in parameters:
            kwargs["metadata"] = {
                "refinery.phase": phase,
                "refinery.plan.id": plan_id or "",
            }
        result = agent.run_sync(prompt, **kwargs)
        usage = getattr(result, "usage", None)
        record_llm_usage(usage() if callable(usage) else usage, model=model)
        return result
