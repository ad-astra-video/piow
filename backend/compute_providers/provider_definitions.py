"""Provider definitions for dynamic compute provider registration."""

PROVIDER_DEFINITIONS = [
    {
        "name": "livepeer-primary",
        "class_path": "compute_providers.livepeer.livepeer.LivepeerComputeProvider",
        "config": {
            "name": "livepeer-primary",
            "gpu_runner_url": "${LIVEPEER_GATEWAY_URL}",
            "api_key": "${LIVEPEER_API_KEY}",  # For future use if Livepeer needs auth
            "enabled": True
        },
        "is_default": False,
        "tags": ["livepeer", "gpu"]
    },
    {
        "name": "runpod-main",
        "class_path": "compute_providers.runpod.runpod.RunpodComputeProvider",
        "config": {
            "name": "runpod-main",
            "endpoint_id": "${RUNPOD_ENDPOINT_ID}",
            "api_key": "${RUNPOD_API_KEY}",  # Required for Runpod authentication
            "enabled": True
        },
        "is_default": True,  # Set Runpod as default for now
        "tags": ["runpod", "gpu"]
    }
]