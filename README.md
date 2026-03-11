# DigitalOcean Instance Availability Dashboard

Tracks droplet size availability across all DigitalOcean regions and generates a static HTML dashboard.

[![Deploy to HopX Sandbox](https://img.shields.io/badge/Deploy_to-HopX_Sandbox-4c51f7?style=for-the-badge)](https://github.com/alexo-bunnyshell/do-instance-availability/actions/workflows/deploy-hopx.yml)

**Live dashboard:** [do-avail-a4ao4q.bunnyenv.com](https://do-avail-a4ao4q.bunnyenv.com/)

## Quick Start

### Local

```bash
# Set environment variables
export DIGITAL_OCEAN_TOKEN="your-do-token"

# Run the checker
pip install requests python-dotenv
python check_availability.py

# Open dashboard.html in your browser
```

### Deploy to HopX Sandbox

Spins up a Firecracker microVM, runs the checker, and serves the dashboard via HTTP.

```bash
export HOPX_API_KEY="your-hopx-key"
export DIGITAL_OCEAN_TOKEN="your-do-token"

pip install hopx-ai requests python-dotenv
python deploy_sandbox.py
```

Or click the **Deploy to HopX Sandbox** badge above to run it via GitHub Actions (requires `HOPX_API_KEY` and `DIGITAL_OCEAN_TOKEN` as repo secrets).
