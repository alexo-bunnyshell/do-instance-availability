#!/usr/bin/env python3
"""Deploy DO Instance Availability app to a HopX Sandbox."""

import os
from pathlib import Path

from dotenv import load_dotenv
from hopx_ai import Sandbox

# Load keys from local .env (no-op in CI where env vars are already set)
load_dotenv(Path(__file__).parent / ".env")
HOPX_API_KEY = os.getenv("HOPX_API_KEY")
DO_TOKEN = os.getenv("DIGITAL_OCEAN_TOKEN")

if not HOPX_API_KEY:
    raise SystemExit("Error: HOPX_API_KEY not set")
if not DO_TOKEN:
    raise SystemExit("Error: DIGITAL_OCEAN_TOKEN not set")

# Create sandbox with env vars (no context manager - keep alive)
print("Creating HopX sandbox...")
sandbox = Sandbox.create(
    template="code-interpreter",
    api_key=HOPX_API_KEY,
    env_vars={"DIGITAL_OCEAN_TOKEN": DO_TOKEN},
    internet_access=True,
)

info = sandbox.get_info()
print(f"Sandbox ID: {info.sandbox_id}")
print(f"Status: {info.status}")

# Upload check_availability.py
print("\nUploading check_availability.py...")
script_content = (Path(__file__).parent / "check_availability.py").read_text()
sandbox.files.write("/workspace/check_availability.py", script_content)

# Install dependencies
print("Installing dependencies...")
result = sandbox.commands.run("pip3 install requests python-dotenv", timeout=60)
print(result.stdout)
if result.exit_code != 0:
    print(f"STDERR: {result.stderr}")

# Create data directory
sandbox.commands.run("mkdir -p /workspace/data", timeout=10)

# Run the checker script
print("Running availability checker...")
result = sandbox.commands.run(
    "python3 check_availability.py",
    timeout=120,
    working_dir="/workspace",
)
print(result.stdout)
if result.exit_code != 0:
    print(f"STDERR: {result.stderr}")
    raise SystemExit("Checker script failed")

# Start background HTTP server on port 8080
print("Starting HTTP server on port 8080...")
sandbox.commands.run(
    "python3 -m http.server 8080 --bind 0.0.0.0",
    background=True,
    working_dir="/workspace",
)

# Print access info
info = sandbox.get_info()
print(f"\n{'=' * 50}")
print(f"Sandbox ID:  {info.sandbox_id}")
print(f"Status:      {info.status}")
print(f"Public host: {info.public_host}")
print(f"{'=' * 50}")
# Construct port-specific URL
dashboard_url = info.public_host.replace("7777-", "8080-")
print(f"Dashboard:   {dashboard_url}/dashboard.html")
print(f"{'=' * 50}")
print(f"\nTo stop the sandbox later:")
print(f"  python3 -c \"from hopx_ai import Sandbox; Sandbox.connect('{info.sandbox_id}').kill()\"")

# Write to GitHub Actions step summary if running in CI
summary_path = os.getenv("GITHUB_STEP_SUMMARY")
if summary_path:
    with open(summary_path, "a") as f:
        f.write(f"## HopX Sandbox Deployed\n\n")
        f.write(f"- **Sandbox ID:** `{info.sandbox_id}`\n")
        f.write(f"- **Dashboard:** [{dashboard_url}/dashboard.html]({dashboard_url}/dashboard.html)\n")
        f.write(f"\nTo tear down: `Sandbox.connect('{info.sandbox_id}').kill()`\n")
