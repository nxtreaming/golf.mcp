<div align="center">
  <img src="./golf-banner.png" alt="Golf Banner">
  
  <br>
  
  <h1 align="center">
    <br>
    <span style="font-size: 80px;">⛳ Golf</span>
    <br>
  </h1>
  
  <h3 align="center">
    Easiest framework for building MCP servers
  </h3>
  
  <br>
  
  <p>
    <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
    <a href="https://github.com/golf-mcp/golf/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg" alt="PRs"></a>
    <a href="https://github.com/golf-mcp/golf/issues"><img src="https://img.shields.io/badge/support-contact%20author-purple.svg" alt="Support"></a>
  </p>
  
  <p>
    <a href="https://docs.golf.dev"><strong>📚 Documentation</strong></a>
  </p>
</div>

## Overview

Golf is a **framework** designed to streamline the creation of MCP server applications. It allows developers to define server's capabilities—*tools*, *prompts*, and *resources*—as simple Python files within a conventional directory structure. Golf then automatically discovers, parses, and compiles these components into a runnable FastMCP server, minimizing boilerplate and accelerating development.

With Golf, you can focus on implementing your agent's logic rather than wrestling with server setup and integration complexities. It's built for developers who want a quick, organized way to build powerful MCP servers.

## Quick Start

Get your Golf project up and running in a few simple steps:

### 1. Install Golf

Ensure you have Python (3.10+ recommended) installed. Then, install Golf using pip:

```bash
pip install golf-mcp
```

### 2. Initialize Your Project

Use the Golf CLI to scaffold a new project:

```bash
golf init your-project-name
```
This command creates a new directory (`your-project-name`) with a basic project structure, including example tools, resources, and a `golf.json` configuration file.

### 3. Run the Development Server

Navigate into your new project directory and start the development server:

```bash
cd your-project-name
golf build dev
golf run
```
This will start the FastMCP server, typically on `http://127.0.0.1:3000` (configurable in `golf.json`).

That's it! Your Golf server is running and ready for integration.

## Basic Project Structure

A Golf project initialized with `golf init` will have a structure similar to this:

```
<your-project-name>/
│
├─ golf.json          # Main project configuration
│
├─ tools/             # Directory for tool implementations
│   └─ hello.py       # Example tool
│
├─ resources/         # Directory for resource implementations
│   └─ info.py        # Example resource
│
├─ prompts/           # Directory for prompt templates
│   └─ welcome.py     # Example prompt
│
├─ .env               # Environment variables (e.g., API keys, server port)
└─ pre_build.py       # (Optional) Script for pre-build hooks (e.g., auth setup)
```

-   **`golf.json`**: Configures server name, port, transport, telemetry, and other build settings.
-   **`tools/`**, **`resources/`**, **`prompts/`**: Contain your Python files, each defining a single component. These directories can also contain nested subdirectories to further organize your components (e.g., `tools/payments/charge.py`). The module docstring of each file serves as the component's description.
    -   Component IDs are automatically derived from their file path. For example, `tools/hello.py` becomes `hello`, and a nested file like `tools/payments/submit.py` would become `submit_payments` (filename, followed by reversed parent directories under the main category, joined by underscores).
-   **`common.py`** (not shown, but can be placed in subdirectories like `tools/payments/common.py`): Used to share code (clients, models, etc.) among components in the same subdirectory.

## Example: Defining a Tool

Creating a new tool is as simple as adding a Python file to the `tools/` directory. The example `tools/hello.py` in the boilerplate looks like this:

```python
# tools/hello.py
"""Hello World tool {{project_name}}."""

from typing import Annotated
from pydantic import BaseModel, Field

class Output(BaseModel):
    """Response from the hello tool."""
    message: str

async def hello(
    name: Annotated[str, Field(description="The name of the person to greet")] = "World",
    greeting: Annotated[str, Field(description="The greeting phrase to use")] = "Hello"
) -> Output:
    """Say hello to the given name.
    
    This is a simple example tool that demonstrates the basic structure
    of a tool implementation in Golf.
    """
    print(f"{greeting} {name}...")
    return Output(message=f"{greeting}, {name}!")

# Designate the entry point function
export = hello
```
Golf will automatically discover this file. The module docstring `"""Hello World tool {{project_name}}."""` is used as the tool's description. It infers parameters from the `hello` function's signature and uses the `Output` Pydantic model for the output schema. The tool will be registered with the ID `hello`.

## Configuration (`golf.json`)

The `golf.json` file is the heart of your Golf project configuration. Here's what each field controls:

```jsonc
{
  "name": "{{project_name}}",          // Your MCP server name (required)
  "description": "A Golf project",     // Brief description of your server's purpose
  "host": "127.0.0.1",                // Server host - use "0.0.0.0" to listen on all interfaces
  "port": 3000,                       // Server port - any available port number
  "transport": "sse",                 // Communication protocol:
                                      // - "sse": Server-Sent Events (recommended for web clients)
                                      // - "streamable-http": HTTP with streaming support
                                      // - "stdio": Standard I/O (for CLI integration)
  
  // HTTP Transport Configuration (optional)
  "stateless_http": false,            // Make streamable-http transport stateless (new session per request)
                                      // When true, server restarts won't break existing client connections
  
  // Health Check Configuration (optional)
  "health_check_enabled": false,      // Enable health check endpoint for Kubernetes/load balancers
  "health_check_path": "/health",     // HTTP path for health check endpoint
  "health_check_response": "OK",      // Response text returned by health check
  
  // OpenTelemetry Configuration (optional)
  "opentelemetry_enabled": false,     // Enable distributed tracing
  "opentelemetry_default_exporter": "console"  // Default exporter if OTEL_TRACES_EXPORTER not set
                                               // Options: "console", "otlp_http"
}
```

### Key Configuration Options:

- **`name`**: The identifier for your MCP server. This will be shown to clients connecting to your server.
- **`transport`**: Choose based on your client needs:
  - `"sse"` is ideal for web-based clients and real-time communication
  - `"streamable-http"` provides HTTP streaming for traditional API clients
  - `"stdio"` enables integration with command-line tools and scripts
- **`host` & `port`**: Control where your server listens. Use `"127.0.0.1"` for local development or `"0.0.0.0"` to accept external connections.
- **`stateless_http`**: When true, makes the streamable-http transport stateless by creating a new session for each request. This ensures that server restarts don't break existing client connections, making the server truly stateless.
- **`health_check_enabled`**: When true, enables a health check endpoint for Kubernetes readiness/liveness probes and load balancers
- **`health_check_path`**: Customizable path for the health check endpoint (defaults to "/health")
- **`health_check_response`**: Customizable response text for successful health checks (defaults to "OK")
- **`opentelemetry_enabled`**: When true, enables distributed tracing for debugging and monitoring your MCP server
- **`opentelemetry_default_exporter`**: Sets the default trace exporter. Can be overridden by the `OTEL_TRACES_EXPORTER` environment variable

## Features

### 🏥 Health Check Support

Golf includes built-in health check endpoint support for production deployments. When enabled, it automatically adds a custom HTTP route that can be used by:
- Kubernetes readiness and liveness probes
- Load balancers and reverse proxies
- Monitoring systems
- Container orchestration platforms

#### Configuration

Enable health checks in your `golf.json`:
```json
{
  "health_check_enabled": true,
  "health_check_path": "/health",
  "health_check_response": "Service is healthy"
}
```

The generated server will include a route like:
```python
@mcp.custom_route('/health', methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Health check endpoint for Kubernetes and load balancers."""
    return PlainTextResponse("Service is healthy")
```

### 🔍 OpenTelemetry Support

Golf includes built-in OpenTelemetry instrumentation for distributed tracing. When enabled, it automatically traces:
- Tool executions with arguments and results
- Resource reads and template expansions
- Prompt generations
- HTTP requests and sessions

#### Configuration

Enable OpenTelemetry in your `golf.json`:
```json
{
  "opentelemetry_enabled": true,
  "opentelemetry_default_exporter": "otlp_http"
}
```

Then configure via environment variables:
```bash
# For OTLP HTTP exporter (e.g., Jaeger, Grafana Tempo)
OTEL_TRACES_EXPORTER=otlp_http
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces
OTEL_SERVICE_NAME=my-golf-server  # Optional, defaults to project name

# For console exporter (debugging)
OTEL_TRACES_EXPORTER=console
```

**Note**: When using the OTLP HTTP exporter, you must set `OTEL_EXPORTER_OTLP_ENDPOINT`. If not configured, Golf will display a warning and disable tracing to avoid errors.

## Roadmap

Here are the things we are working hard on:

*   **`golf deploy` command for one click deployments to Vercel, Blaxel and other providers**
*   **Production-ready OAuth token management, to allow for persistent, encrypted token storage and client mapping**


## Privacy & Telemetry

Golf collects **anonymous** usage data on the CLI to help us understand how the framework is being used and improve it over time. The data collected includes:

- Commands run (init, build, run)
- Success/failure status (no error details)
- Golf version, Python version (major.minor only), and OS type
- Template name (for init command only)
- Build environment (dev/prod for build commands only)

**No personal information, project names, code content, or error messages are ever collected.**

### Opting Out

You can disable telemetry in several ways:

1. **Using the telemetry command** (recommended):
   ```bash
   golf telemetry disable
   ```
   This saves your preference permanently. To re-enable:
   ```bash
   golf telemetry enable
   ```

2. **During any command**: Add `--no-telemetry` to save your preference:
   ```bash
   golf init my-project --no-telemetry
   ```

Your telemetry preference is stored in `~/.golf/telemetry.json` and persists across all Golf commands.

<div align="center">
Made with ❤️ in Warsaw, Poland and SF
</div>