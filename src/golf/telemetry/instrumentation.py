"""Component-level OpenTelemetry instrumentation for Golf-built servers."""

import asyncio
import functools
import os
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from opentelemetry import baggage, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

T = TypeVar("T")

# Global tracer instance
_tracer: trace.Tracer | None = None
_provider: TracerProvider | None = None


def init_telemetry(service_name: str = "golf-mcp-server") -> TracerProvider | None:
    """Initialize OpenTelemetry with environment-based configuration.

    Returns None if required environment variables are not set.
    """
    global _provider

    # Check for Golf platform integration first
    golf_api_key = os.environ.get("GOLF_API_KEY")
    if golf_api_key and not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # Auto-configure for Golf platform
        os.environ["OTEL_TRACES_EXPORTER"] = "otlp_http"
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:8000/api/v1/otel"
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"X-Golf-Key={golf_api_key}"
        print("[INFO] Auto-configured OpenTelemetry for Golf platform ingestion")

    # Check for required environment variables based on exporter type
    exporter_type = os.environ.get("OTEL_TRACES_EXPORTER", "console").lower()

    # For OTLP HTTP exporter, check if endpoint is configured
    if exporter_type == "otlp_http":
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            print(
                "[WARNING] OpenTelemetry tracing is disabled: "
                "OTEL_EXPORTER_OTLP_ENDPOINT is not set for OTLP HTTP exporter"
            )
            return None

    # Create resource with service information
    resource_attributes = {
        "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
        "service.version": os.environ.get("SERVICE_VERSION", "1.0.0"),
        "service.instance.id": os.environ.get("SERVICE_INSTANCE_ID", "default"),
    }

    # Add Golf-specific attributes if available
    if golf_api_key:
        golf_server_id = os.environ.get("GOLF_SERVER_ID")
        if golf_server_id:
            resource_attributes["golf.server.id"] = golf_server_id
        resource_attributes["golf.platform.enabled"] = "true"

    resource = Resource.create(resource_attributes)

    # Create provider
    provider = TracerProvider(resource=resource)

    # Configure exporter based on type
    try:
        if exporter_type == "otlp_http":
            endpoint = os.environ.get(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces"
            )
            headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")

            # Parse headers if provided
            header_dict = {}
            if headers:
                for header in headers.split(","):
                    if "=" in header:
                        key, value = header.split("=", 1)
                        header_dict[key.strip()] = value.strip()

            exporter = OTLPSpanExporter(
                endpoint=endpoint, headers=header_dict if header_dict else None
            )

            # Log successful configuration for Golf platform
            if golf_api_key:
                print(f"[INFO] OpenTelemetry configured for Golf platform: {endpoint}")
        else:
            # Default to console exporter
            exporter = ConsoleSpanExporter(out=sys.stderr)
    except Exception:
        import traceback

        traceback.print_exc()
        raise

    # Add batch processor for better performance
    try:
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            schedule_delay_millis=1000,  # Export every 1 second instead of default 5 seconds
            max_export_batch_size=512,
            export_timeout_millis=5000,
        )
        provider.add_span_processor(processor)
    except Exception:
        import traceback

        traceback.print_exc()
        raise

    # Set as global provider
    try:
        # Check if a provider is already set to avoid the warning
        existing_provider = trace.get_tracer_provider()
        if (
            existing_provider is None
            or str(type(existing_provider).__name__) == "ProxyTracerProvider"
        ):
            # Only set if no provider exists or it's the default proxy provider
            trace.set_tracer_provider(provider)
        _provider = provider
    except Exception:
        import traceback

        traceback.print_exc()
        raise

    return provider


def get_tracer() -> trace.Tracer:
    """Get or create the global tracer instance."""
    global _tracer, _provider

    # If no provider is set, telemetry is disabled - return no-op tracer
    if _provider is None:
        return trace.get_tracer("golf.mcp.components.noop", "1.0.0")

    if _tracer is None:
        _tracer = trace.get_tracer("golf.mcp.components", "1.0.0")
    return _tracer


def instrument_tool(func: Callable[..., T], tool_name: str) -> Callable[..., T]:
    """Instrument a tool function with OpenTelemetry tracing."""
    global _provider

    # If telemetry is disabled, return the original function
    if _provider is None:
        return func

    tracer = get_tracer()

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        # Record metrics timing
        import time

        start_time = time.time()

        # Create a more descriptive span name
        span_name = f"mcp.tool.{tool_name}.execute"

        # start_as_current_span automatically uses the current context and manages it
        with tracer.start_as_current_span(span_name) as span:
            # Add comprehensive attributes
            span.set_attribute("mcp.component.type", "tool")
            span.set_attribute("mcp.component.name", tool_name)
            span.set_attribute("mcp.tool.name", tool_name)
            span.set_attribute("mcp.tool.function", func.__name__)
            span.set_attribute(
                "mcp.tool.module",
                func.__module__ if hasattr(func, "__module__") else "unknown",
            )

            # Add execution context
            span.set_attribute("mcp.execution.args_count", len(args))
            span.set_attribute("mcp.execution.kwargs_count", len(kwargs))
            span.set_attribute("mcp.execution.async", True)

            # Extract Context parameter if present
            ctx = kwargs.get("ctx")
            if ctx:
                # Only extract known MCP context attributes
                ctx_attrs = [
                    "request_id",
                    "session_id",
                    "client_id",
                    "user_id",
                    "tenant_id",
                ]
                for attr in ctx_attrs:
                    if hasattr(ctx, attr):
                        value = getattr(ctx, attr)
                        if value is not None:
                            span.set_attribute(f"mcp.context.{attr}", str(value))

            # Also check baggage for session ID
            session_id_from_baggage = baggage.get_baggage("mcp.session.id")
            if session_id_from_baggage:
                span.set_attribute("mcp.session.id", session_id_from_baggage)

            # Add event for tool execution start
            span.add_event("tool.execution.started", {"tool.name": tool_name})

            try:
                result = await func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))

                # Add event for successful completion
                span.add_event("tool.execution.completed", {"tool.name": tool_name})

                # Record metrics for successful execution
                try:
                    from golf.metrics import get_metrics_collector

                    metrics_collector = get_metrics_collector()
                    metrics_collector.increment_tool_execution(tool_name, "success")
                    metrics_collector.record_tool_duration(
                        tool_name, time.time() - start_time
                    )
                except ImportError:
                    # Metrics not available, continue without metrics
                    pass

                # Capture result metadata with better structure
                if result is not None:
                    if isinstance(result, str | int | float | bool):
                        span.set_attribute("mcp.tool.result.value", str(result))
                        span.set_attribute(
                            "mcp.tool.result.type", type(result).__name__
                        )
                    elif isinstance(result, list):
                        span.set_attribute("mcp.tool.result.count", len(result))
                        span.set_attribute("mcp.tool.result.type", "array")
                    elif isinstance(result, dict):
                        span.set_attribute("mcp.tool.result.count", len(result))
                        span.set_attribute("mcp.tool.result.type", "object")
                        # Only show first few keys to avoid exceeding attribute limits
                        if len(result) > 0 and len(result) <= 5:
                            keys_list = list(result.keys())[:5]
                            # Limit key length and join
                            truncated_keys = [
                                str(k)[:20] + "..." if len(str(k)) > 20 else str(k)
                                for k in keys_list
                            ]
                            span.set_attribute(
                                "mcp.tool.result.sample_keys", ",".join(truncated_keys)
                            )
                    elif hasattr(result, "__len__"):
                        span.set_attribute("mcp.tool.result.length", len(result))

                    # For any result, record its type
                    span.set_attribute("mcp.tool.result.class", type(result).__name__)

                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))

                # Add event for error
                span.add_event(
                    "tool.execution.error",
                    {
                        "tool.name": tool_name,
                        "error.type": type(e).__name__,
                        "error.message": str(e),
                    },
                )

                # Record metrics for failed execution
                try:
                    from golf.metrics import get_metrics_collector

                    metrics_collector = get_metrics_collector()
                    metrics_collector.increment_tool_execution(tool_name, "error")
                    metrics_collector.increment_error("tool", type(e).__name__)
                except ImportError:
                    # Metrics not available, continue without metrics
                    pass

                raise

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        # Record metrics timing
        import time

        start_time = time.time()

        # Create a more descriptive span name
        span_name = f"mcp.tool.{tool_name}.execute"

        # start_as_current_span automatically uses the current context and manages it
        with tracer.start_as_current_span(span_name) as span:
            # Add comprehensive attributes
            span.set_attribute("mcp.component.type", "tool")
            span.set_attribute("mcp.component.name", tool_name)
            span.set_attribute("mcp.tool.name", tool_name)
            span.set_attribute("mcp.tool.function", func.__name__)
            span.set_attribute(
                "mcp.tool.module",
                func.__module__ if hasattr(func, "__module__") else "unknown",
            )

            # Add execution context
            span.set_attribute("mcp.execution.args_count", len(args))
            span.set_attribute("mcp.execution.kwargs_count", len(kwargs))
            span.set_attribute("mcp.execution.async", False)

            # Extract Context parameter if present
            ctx = kwargs.get("ctx")
            if ctx:
                # Only extract known MCP context attributes
                ctx_attrs = [
                    "request_id",
                    "session_id",
                    "client_id",
                    "user_id",
                    "tenant_id",
                ]
                for attr in ctx_attrs:
                    if hasattr(ctx, attr):
                        value = getattr(ctx, attr)
                        if value is not None:
                            span.set_attribute(f"mcp.context.{attr}", str(value))

            # Also check baggage for session ID
            session_id_from_baggage = baggage.get_baggage("mcp.session.id")
            if session_id_from_baggage:
                span.set_attribute("mcp.session.id", session_id_from_baggage)

            # Add event for tool execution start
            span.add_event("tool.execution.started", {"tool.name": tool_name})

            try:
                result = func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))

                # Add event for successful completion
                span.add_event("tool.execution.completed", {"tool.name": tool_name})

                # Record metrics for successful execution
                try:
                    from golf.metrics import get_metrics_collector

                    metrics_collector = get_metrics_collector()
                    metrics_collector.increment_tool_execution(tool_name, "success")
                    metrics_collector.record_tool_duration(
                        tool_name, time.time() - start_time
                    )
                except ImportError:
                    # Metrics not available, continue without metrics
                    pass

                # Capture result metadata with better structure
                if result is not None:
                    if isinstance(result, str | int | float | bool):
                        span.set_attribute("mcp.tool.result.value", str(result))
                        span.set_attribute(
                            "mcp.tool.result.type", type(result).__name__
                        )
                    elif isinstance(result, list):
                        span.set_attribute("mcp.tool.result.count", len(result))
                        span.set_attribute("mcp.tool.result.type", "array")
                    elif isinstance(result, dict):
                        span.set_attribute("mcp.tool.result.count", len(result))
                        span.set_attribute("mcp.tool.result.type", "object")
                        # Only show first few keys to avoid exceeding attribute limits
                        if len(result) > 0 and len(result) <= 5:
                            keys_list = list(result.keys())[:5]
                            # Limit key length and join
                            truncated_keys = [
                                str(k)[:20] + "..." if len(str(k)) > 20 else str(k)
                                for k in keys_list
                            ]
                            span.set_attribute(
                                "mcp.tool.result.sample_keys", ",".join(truncated_keys)
                            )
                    elif hasattr(result, "__len__"):
                        span.set_attribute("mcp.tool.result.length", len(result))

                    # For any result, record its type
                    span.set_attribute("mcp.tool.result.class", type(result).__name__)

                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))

                # Add event for error
                span.add_event(
                    "tool.execution.error",
                    {
                        "tool.name": tool_name,
                        "error.type": type(e).__name__,
                        "error.message": str(e),
                    },
                )

                # Record metrics for failed execution
                try:
                    from golf.metrics import get_metrics_collector

                    metrics_collector = get_metrics_collector()
                    metrics_collector.increment_tool_execution(tool_name, "error")
                    metrics_collector.increment_error("tool", type(e).__name__)
                except ImportError:
                    # Metrics not available, continue without metrics
                    pass

                raise

    # Return appropriate wrapper based on function type
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


def instrument_resource(func: Callable[..., T], resource_uri: str) -> Callable[..., T]:
    """Instrument a resource function with OpenTelemetry tracing."""
    global _provider

    # If telemetry is disabled, return the original function
    if _provider is None:
        return func

    tracer = get_tracer()

    # Determine if this is a template based on URI pattern
    is_template = "{" in resource_uri

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        # Create a more descriptive span name
        span_name = f"mcp.resource.{'template' if is_template else 'static'}.read"
        with tracer.start_as_current_span(span_name) as span:
            # Add comprehensive attributes
            span.set_attribute("mcp.component.type", "resource")
            span.set_attribute("mcp.component.name", resource_uri)
            span.set_attribute("mcp.resource.uri", resource_uri)
            span.set_attribute("mcp.resource.is_template", is_template)
            span.set_attribute("mcp.resource.function", func.__name__)
            span.set_attribute(
                "mcp.resource.module",
                func.__module__ if hasattr(func, "__module__") else "unknown",
            )
            span.set_attribute("mcp.execution.async", True)

            # Extract Context parameter if present
            ctx = kwargs.get("ctx")
            if ctx:
                # Only extract known MCP context attributes
                ctx_attrs = [
                    "request_id",
                    "session_id",
                    "client_id",
                    "user_id",
                    "tenant_id",
                ]
                for attr in ctx_attrs:
                    if hasattr(ctx, attr):
                        value = getattr(ctx, attr)
                        if value is not None:
                            span.set_attribute(f"mcp.context.{attr}", str(value))

            # Also check baggage for session ID
            session_id_from_baggage = baggage.get_baggage("mcp.session.id")
            if session_id_from_baggage:
                span.set_attribute("mcp.session.id", session_id_from_baggage)

            # Add event for resource read start
            span.add_event("resource.read.started", {"resource.uri": resource_uri})

            try:
                result = await func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))

                # Add event for successful read
                span.add_event(
                    "resource.read.completed", {"resource.uri": resource_uri}
                )

                # Add result metadata
                if hasattr(result, "__len__"):
                    span.set_attribute("mcp.resource.result.size", len(result))

                # Determine content type if possible
                if isinstance(result, str):
                    span.set_attribute("mcp.resource.result.type", "text")
                    span.set_attribute("mcp.resource.result.length", len(result))
                elif isinstance(result, bytes):
                    span.set_attribute("mcp.resource.result.type", "binary")
                    span.set_attribute("mcp.resource.result.size_bytes", len(result))
                elif isinstance(result, dict):
                    span.set_attribute("mcp.resource.result.type", "object")
                    span.set_attribute("mcp.resource.result.keys_count", len(result))
                elif isinstance(result, list):
                    span.set_attribute("mcp.resource.result.type", "array")
                    span.set_attribute("mcp.resource.result.items_count", len(result))

                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))

                # Add event for error
                span.add_event(
                    "resource.read.error",
                    {
                        "resource.uri": resource_uri,
                        "error.type": type(e).__name__,
                        "error.message": str(e),
                    },
                )
                raise

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        # Create a more descriptive span name
        span_name = f"mcp.resource.{'template' if is_template else 'static'}.read"
        with tracer.start_as_current_span(span_name) as span:
            # Add comprehensive attributes
            span.set_attribute("mcp.component.type", "resource")
            span.set_attribute("mcp.component.name", resource_uri)
            span.set_attribute("mcp.resource.uri", resource_uri)
            span.set_attribute("mcp.resource.is_template", is_template)
            span.set_attribute("mcp.resource.function", func.__name__)
            span.set_attribute(
                "mcp.resource.module",
                func.__module__ if hasattr(func, "__module__") else "unknown",
            )
            span.set_attribute("mcp.execution.async", False)

            # Extract Context parameter if present
            ctx = kwargs.get("ctx")
            if ctx:
                # Only extract known MCP context attributes
                ctx_attrs = [
                    "request_id",
                    "session_id",
                    "client_id",
                    "user_id",
                    "tenant_id",
                ]
                for attr in ctx_attrs:
                    if hasattr(ctx, attr):
                        value = getattr(ctx, attr)
                        if value is not None:
                            span.set_attribute(f"mcp.context.{attr}", str(value))

            # Also check baggage for session ID
            session_id_from_baggage = baggage.get_baggage("mcp.session.id")
            if session_id_from_baggage:
                span.set_attribute("mcp.session.id", session_id_from_baggage)

            # Add event for resource read start
            span.add_event("resource.read.started", {"resource.uri": resource_uri})

            try:
                result = func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))

                # Add event for successful read
                span.add_event(
                    "resource.read.completed", {"resource.uri": resource_uri}
                )

                # Add result metadata
                if hasattr(result, "__len__"):
                    span.set_attribute("mcp.resource.result.size", len(result))

                # Determine content type if possible
                if isinstance(result, str):
                    span.set_attribute("mcp.resource.result.type", "text")
                    span.set_attribute("mcp.resource.result.length", len(result))
                elif isinstance(result, bytes):
                    span.set_attribute("mcp.resource.result.type", "binary")
                    span.set_attribute("mcp.resource.result.size_bytes", len(result))
                elif isinstance(result, dict):
                    span.set_attribute("mcp.resource.result.type", "object")
                    span.set_attribute("mcp.resource.result.keys_count", len(result))
                elif isinstance(result, list):
                    span.set_attribute("mcp.resource.result.type", "array")
                    span.set_attribute("mcp.resource.result.items_count", len(result))

                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))

                # Add event for error
                span.add_event(
                    "resource.read.error",
                    {
                        "resource.uri": resource_uri,
                        "error.type": type(e).__name__,
                        "error.message": str(e),
                    },
                )
                raise

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


def instrument_prompt(func: Callable[..., T], prompt_name: str) -> Callable[..., T]:
    """Instrument a prompt function with OpenTelemetry tracing."""
    global _provider

    # If telemetry is disabled, return the original function
    if _provider is None:
        return func

    tracer = get_tracer()

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        # Create a more descriptive span name
        span_name = f"mcp.prompt.{prompt_name}.generate"
        with tracer.start_as_current_span(span_name) as span:
            # Add comprehensive attributes
            span.set_attribute("mcp.component.type", "prompt")
            span.set_attribute("mcp.component.name", prompt_name)
            span.set_attribute("mcp.prompt.name", prompt_name)
            span.set_attribute("mcp.prompt.function", func.__name__)
            span.set_attribute(
                "mcp.prompt.module",
                func.__module__ if hasattr(func, "__module__") else "unknown",
            )
            span.set_attribute("mcp.execution.async", True)

            # Extract Context parameter if present
            ctx = kwargs.get("ctx")
            if ctx:
                # Only extract known MCP context attributes
                ctx_attrs = [
                    "request_id",
                    "session_id",
                    "client_id",
                    "user_id",
                    "tenant_id",
                ]
                for attr in ctx_attrs:
                    if hasattr(ctx, attr):
                        value = getattr(ctx, attr)
                        if value is not None:
                            span.set_attribute(f"mcp.context.{attr}", str(value))

            # Also check baggage for session ID
            session_id_from_baggage = baggage.get_baggage("mcp.session.id")
            if session_id_from_baggage:
                span.set_attribute("mcp.session.id", session_id_from_baggage)

            # Add event for prompt generation start
            span.add_event("prompt.generation.started", {"prompt.name": prompt_name})

            try:
                result = await func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))

                # Add event for successful generation
                span.add_event(
                    "prompt.generation.completed", {"prompt.name": prompt_name}
                )

                # Add message count and type information
                if isinstance(result, list):
                    span.set_attribute("mcp.prompt.result.message_count", len(result))
                    span.set_attribute("mcp.prompt.result.type", "message_list")

                    # Analyze message types if they have role attributes
                    roles = []
                    for msg in result:
                        if hasattr(msg, "role"):
                            roles.append(msg.role)
                        elif isinstance(msg, dict) and "role" in msg:
                            roles.append(msg["role"])

                    if roles:
                        unique_roles = list(set(roles))
                        span.set_attribute(
                            "mcp.prompt.result.roles", ",".join(unique_roles)
                        )
                        span.set_attribute(
                            "mcp.prompt.result.role_counts",
                            str({role: roles.count(role) for role in unique_roles}),
                        )
                elif isinstance(result, str):
                    span.set_attribute("mcp.prompt.result.type", "string")
                    span.set_attribute("mcp.prompt.result.length", len(result))
                else:
                    span.set_attribute("mcp.prompt.result.type", type(result).__name__)

                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))

                # Add event for error
                span.add_event(
                    "prompt.generation.error",
                    {
                        "prompt.name": prompt_name,
                        "error.type": type(e).__name__,
                        "error.message": str(e),
                    },
                )
                raise

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        # Create a more descriptive span name
        span_name = f"mcp.prompt.{prompt_name}.generate"
        with tracer.start_as_current_span(span_name) as span:
            # Add comprehensive attributes
            span.set_attribute("mcp.component.type", "prompt")
            span.set_attribute("mcp.component.name", prompt_name)
            span.set_attribute("mcp.prompt.name", prompt_name)
            span.set_attribute("mcp.prompt.function", func.__name__)
            span.set_attribute(
                "mcp.prompt.module",
                func.__module__ if hasattr(func, "__module__") else "unknown",
            )
            span.set_attribute("mcp.execution.async", False)

            # Extract Context parameter if present
            ctx = kwargs.get("ctx")
            if ctx:
                # Only extract known MCP context attributes
                ctx_attrs = [
                    "request_id",
                    "session_id",
                    "client_id",
                    "user_id",
                    "tenant_id",
                ]
                for attr in ctx_attrs:
                    if hasattr(ctx, attr):
                        value = getattr(ctx, attr)
                        if value is not None:
                            span.set_attribute(f"mcp.context.{attr}", str(value))

            # Also check baggage for session ID
            session_id_from_baggage = baggage.get_baggage("mcp.session.id")
            if session_id_from_baggage:
                span.set_attribute("mcp.session.id", session_id_from_baggage)

            # Add event for prompt generation start
            span.add_event("prompt.generation.started", {"prompt.name": prompt_name})

            try:
                result = func(*args, **kwargs)
                span.set_status(Status(StatusCode.OK))

                # Add event for successful generation
                span.add_event(
                    "prompt.generation.completed", {"prompt.name": prompt_name}
                )

                # Add message count and type information
                if isinstance(result, list):
                    span.set_attribute("mcp.prompt.result.message_count", len(result))
                    span.set_attribute("mcp.prompt.result.type", "message_list")

                    # Analyze message types if they have role attributes
                    roles = []
                    for msg in result:
                        if hasattr(msg, "role"):
                            roles.append(msg.role)
                        elif isinstance(msg, dict) and "role" in msg:
                            roles.append(msg["role"])

                    if roles:
                        unique_roles = list(set(roles))
                        span.set_attribute(
                            "mcp.prompt.result.roles", ",".join(unique_roles)
                        )
                        span.set_attribute(
                            "mcp.prompt.result.role_counts",
                            str({role: roles.count(role) for role in unique_roles}),
                        )
                elif isinstance(result, str):
                    span.set_attribute("mcp.prompt.result.type", "string")
                    span.set_attribute("mcp.prompt.result.length", len(result))
                else:
                    span.set_attribute("mcp.prompt.result.type", type(result).__name__)

                return result
            except Exception as e:
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, str(e)))

                # Add event for error
                span.add_event(
                    "prompt.generation.error",
                    {
                        "prompt.name": prompt_name,
                        "error.type": type(e).__name__,
                        "error.message": str(e),
                    },
                )
                raise

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


@asynccontextmanager
async def telemetry_lifespan(mcp_instance):
    """Simplified lifespan for telemetry initialization and cleanup."""
    global _provider

    # Initialize telemetry with the server name
    provider = init_telemetry(service_name=mcp_instance.name)

    # If provider is None, telemetry is disabled
    if provider is None:
        # Just yield without any telemetry setup
        yield
        return

    # Try to add session tracking middleware if possible
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request

        class SessionTracingMiddleware(BaseHTTPMiddleware):
            def __init__(self, app):
                super().__init__(app)
                # Track seen sessions to count unique sessions
                self.seen_sessions = set()
                # Track session start times for duration calculation
                self.session_start_times = {}

            async def dispatch(self, request: Request, call_next):
                # Record HTTP request timing
                import time

                start_time = time.time()

                # Extract session ID from query params or headers
                session_id = request.query_params.get("session_id")
                if not session_id:
                    # Check headers as fallback
                    session_id = request.headers.get("x-session-id")

                # Track session metrics
                if session_id:
                    current_time = time.time()

                    # Record new session if we haven't seen this session ID before
                    if session_id not in self.seen_sessions:
                        self.seen_sessions.add(session_id)
                        self.session_start_times[session_id] = current_time
                        try:
                            from golf.metrics import get_metrics_collector

                            metrics_collector = get_metrics_collector()
                            metrics_collector.increment_session()
                        except ImportError:
                            pass
                    else:
                        # Update session duration (time since first request)
                        if session_id in self.session_start_times:
                            duration = (
                                current_time - self.session_start_times[session_id]
                            )
                            try:
                                from golf.metrics import get_metrics_collector

                                metrics_collector = get_metrics_collector()
                                metrics_collector.record_session_duration(duration)
                            except ImportError:
                                pass

                    # Clean up old session data periodically
                    if len(self.seen_sessions) > 10000:
                        # Keep only the most recent 5000 sessions
                        recent_sessions = list(self.seen_sessions)[-5000:]
                        self.seen_sessions = set(recent_sessions)
                        # Clean up start times for removed sessions
                        for old_session in list(self.session_start_times.keys()):
                            if old_session not in self.seen_sessions:
                                self.session_start_times.pop(old_session, None)

                # Create a descriptive span name based on the request
                method = request.method
                path = request.url.path

                # Determine the operation type from the path
                operation_type = "unknown"
                if "/mcp" in path:
                    operation_type = "mcp.request"
                elif "/sse" in path:
                    operation_type = "sse.stream"
                elif "/auth" in path:
                    operation_type = "auth"

                span_name = f"{operation_type}.{method.lower()}"

                tracer = get_tracer()
                with tracer.start_as_current_span(span_name) as span:
                    # Add comprehensive HTTP attributes
                    span.set_attribute("http.method", method)
                    span.set_attribute("http.url", str(request.url))
                    span.set_attribute("http.scheme", request.url.scheme)
                    span.set_attribute("http.host", request.url.hostname or "unknown")
                    span.set_attribute("http.target", path)
                    span.set_attribute(
                        "http.user_agent", request.headers.get("user-agent", "unknown")
                    )

                    # Add session tracking
                    if session_id:
                        span.set_attribute("mcp.session.id", session_id)
                        # Add to baggage for propagation
                        ctx = baggage.set_baggage("mcp.session.id", session_id)
                        from opentelemetry import context

                        token = context.attach(ctx)
                    else:
                        token = None

                    # Add request size if available
                    content_length = request.headers.get("content-length")
                    if content_length:
                        span.set_attribute("http.request.size", int(content_length))

                    # Add event for request start
                    span.add_event(
                        "http.request.started", {"method": method, "path": path}
                    )

                    try:
                        response = await call_next(request)

                        # Add response attributes
                        span.set_attribute("http.status_code", response.status_code)
                        span.set_attribute(
                            "http.status_class", f"{response.status_code // 100}xx"
                        )

                        # Set span status based on HTTP status
                        if response.status_code >= 400:
                            span.set_status(
                                Status(StatusCode.ERROR, f"HTTP {response.status_code}")
                            )
                        else:
                            span.set_status(Status(StatusCode.OK))

                        # Add event for request completion
                        span.add_event(
                            "http.request.completed",
                            {
                                "method": method,
                                "path": path,
                                "status_code": response.status_code,
                            },
                        )

                        # Record HTTP request metrics
                        try:
                            from golf.metrics import get_metrics_collector

                            metrics_collector = get_metrics_collector()

                            # Clean up path for metrics (remove query params, normalize)
                            clean_path = path.split("?")[0]  # Remove query parameters
                            if clean_path.startswith("/"):
                                clean_path = (
                                    clean_path[1:] or "root"
                                )  # Remove leading slash, handle root

                            metrics_collector.increment_http_request(
                                method, response.status_code, clean_path
                            )
                            metrics_collector.record_http_duration(
                                method, clean_path, time.time() - start_time
                            )
                        except ImportError:
                            # Metrics not available, continue without metrics
                            pass

                        return response
                    except Exception as e:
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))

                        # Add event for error
                        span.add_event(
                            "http.request.error",
                            {
                                "method": method,
                                "path": path,
                                "error.type": type(e).__name__,
                                "error.message": str(e),
                            },
                        )

                        # Record HTTP error metrics
                        try:
                            from golf.metrics import get_metrics_collector

                            metrics_collector = get_metrics_collector()

                            # Clean up path for metrics
                            clean_path = path.split("?")[0]
                            if clean_path.startswith("/"):
                                clean_path = clean_path[1:] or "root"

                            metrics_collector.increment_http_request(
                                method, 500, clean_path
                            )  # Assume 500 for exceptions
                            metrics_collector.increment_error("http", type(e).__name__)
                        except ImportError:
                            pass

                        raise
                    finally:
                        if token:
                            context.detach(token)

        # Try to add middleware to FastMCP app if it has Starlette app
        if hasattr(mcp_instance, "app") or hasattr(mcp_instance, "_app"):
            app = getattr(mcp_instance, "app", getattr(mcp_instance, "_app", None))
            if app and hasattr(app, "add_middleware"):
                app.add_middleware(SessionTracingMiddleware)

        # Also try to instrument FastMCP's internal handlers
        if hasattr(mcp_instance, "_tool_manager") and hasattr(
            mcp_instance._tool_manager, "tools"
        ):
            # The tools should already be instrumented when they were registered
            pass

        # Try to patch FastMCP's request handling to ensure context propagation
        if hasattr(mcp_instance, "handle_request"):
            original_handle_request = mcp_instance.handle_request

            async def traced_handle_request(*args, **kwargs):
                tracer = get_tracer()
                with tracer.start_as_current_span("mcp.handle_request") as span:
                    span.set_attribute("mcp.request.handler", "handle_request")
                    return await original_handle_request(*args, **kwargs)

            mcp_instance.handle_request = traced_handle_request

    except Exception:
        # Silently continue if middleware setup fails
        import traceback

        traceback.print_exc()

    try:
        # Yield control back to FastMCP
        yield
    finally:
        # Cleanup - shutdown the provider
        if _provider and hasattr(_provider, "shutdown"):
            _provider.force_flush()
            _provider.shutdown()
            _provider = None
