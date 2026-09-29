"""Deploy or delete the Gemini merchant agent on Vertex AI Agent Engine.

Usage (deploy):
    python deploy.py \\
        --project my-gcp-project \\
        --location us-central1 \\
        --store-name DreamHaus \\
        --google-api-key AIza...

Usage (delete):
    python deploy.py --delete --resource-name projects/123/.../reasoningEngines/456
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make examples/ importable.
_examples_dir = str(Path(__file__).parent.parent.parent)
if _examples_dir not in sys.path:
    sys.path.insert(0, _examples_dir)

_REQUIREMENTS = [
    "google-adk>=2.10.0",
    "google-cloud-aiplatform[preview]>=1.87.0",
    "fastapi",
    "httpx",
    "pydantic>=2",
    "requests",
]


def deploy(
    project: str,
    location: str,
    store_name: str,
    google_api_key: str,
    sfcc_instance_url: str | None = None,
    sfcc_client_id: str | None = None,
    sfcc_client_secret: str | None = None,
    sfcc_site_id: str | None = None,
) -> str:
    """Deploy the merchant agent to Vertex AI Agent Engine.

    Args:
        project: GCP project ID.
        location: GCP region, e.g. ``"us-central1"``.
        store_name: Display name of the store, e.g. ``"DreamHaus"``.
        google_api_key: Google AI Studio API key (``GOOGLE_API_KEY``).
        sfcc_instance_url: Optional SFCC instance URL; required for live data.
        sfcc_client_id: Optional SFCC OCAPI client ID.
        sfcc_client_secret: Optional SFCC OCAPI client secret.
        sfcc_site_id: Optional SFCC site ID.

    Returns:
        The ``resource_name`` of the deployed Reasoning Engine.

    Raises:
        ImportError: If ``google-cloud-aiplatform[preview]`` is not installed.
        RuntimeError: If a required SFCC env var is absent when SFCC vars are
            partially supplied.
    """
    try:
        import vertexai  # type: ignore[import]
        from vertexai.preview import reasoning_engines  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-cloud-aiplatform[preview] is required: "
            "pip install 'google-cloud-aiplatform[preview]>=1.87.0'"
        ) from exc

    vertexai.init(project=project, location=location)
    os.environ["GOOGLE_API_KEY"] = google_api_key

    # Set SFCC env vars when supplied; warn when only some are present.
    sfcc_vars = {
        "SFCC_INSTANCE_URL": sfcc_instance_url,
        "SFCC_CLIENT_ID": sfcc_client_id,
        "SFCC_CLIENT_SECRET": sfcc_client_secret,
        "SFCC_SITE_ID": sfcc_site_id,
    }
    supplied = {k: v for k, v in sfcc_vars.items() if v}
    if supplied and len(supplied) < len(sfcc_vars):
        missing = [k for k, v in sfcc_vars.items() if not v]
        raise RuntimeError(
            f"Partial SFCC configuration: {missing} must also be supplied."
        )
    for key, value in supplied.items():
        os.environ[key] = value  # type: ignore[assignment]

    if not supplied:
        print(
            "Warning: no SFCC env vars supplied — the deployed agent will "
            "return errors for any tool that requires a live backend."
        )

    from app import create_merchant_app  # type: ignore[import]

    app = create_merchant_app(store_name)

    engine = reasoning_engines.ReasoningEngine.create(
        app,
        requirements=_REQUIREMENTS,
        display_name=f"gemini-merchant-{store_name.lower()}",
        description=(
            f"Gemini ADK merchant agent for {store_name} — "
            "Path 3, Vertex AI Agent Engine deployment."
        ),
    )
    print(f"Deployed: {engine.resource_name}")
    return engine.resource_name


def delete(resource_name: str) -> None:
    """Delete a deployed Reasoning Engine by resource name.

    Args:
        resource_name: Full resource path returned by ``deploy()``, e.g.
            ``projects/123/locations/us-central1/reasoningEngines/456``.
    """
    try:
        from vertexai.preview import reasoning_engines  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-cloud-aiplatform[preview] is required: "
            "pip install 'google-cloud-aiplatform[preview]>=1.87.0'"
        ) from exc

    reasoning_engines.ReasoningEngine(resource_name=resource_name).delete()
    print(f"Deleted: {resource_name}")


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy or delete the Gemini merchant agent on Vertex AI Agent Engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--delete", action="store_true", help="Delete a deployed engine.")
    parser.add_argument(
        "--resource-name",
        help="Resource name of the engine to delete (required with --delete).",
    )

    deploy_group = parser.add_argument_group("deploy options")
    deploy_group.add_argument("--project", help="GCP project ID.")
    deploy_group.add_argument("--location", default="us-central1", help="GCP region.")
    deploy_group.add_argument("--store-name", default="DreamHaus", help="Store display name.")
    deploy_group.add_argument("--google-api-key", help="Google AI Studio API key.")
    deploy_group.add_argument("--sfcc-instance-url", help="SFCC instance URL (optional).")
    deploy_group.add_argument("--sfcc-client-id", help="SFCC OCAPI client ID (optional).")
    deploy_group.add_argument("--sfcc-client-secret", help="SFCC OCAPI client secret (optional).")
    deploy_group.add_argument("--sfcc-site-id", help="SFCC site ID (optional).")

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.delete:
        if not args.resource_name:
            print("Error: --resource-name is required with --delete.", file=sys.stderr)
            sys.exit(1)
        delete(args.resource_name)
        return

    missing = [f for f, v in [("--project", args.project), ("--google-api-key", args.google_api_key)] if not v]
    if missing:
        print(f"Error: {', '.join(missing)} required for deployment.", file=sys.stderr)
        sys.exit(1)

    deploy(
        project=args.project,
        location=args.location,
        store_name=args.store_name,
        google_api_key=args.google_api_key,
        sfcc_instance_url=args.sfcc_instance_url,
        sfcc_client_id=args.sfcc_client_id,
        sfcc_client_secret=args.sfcc_client_secret,
        sfcc_site_id=args.sfcc_site_id,
    )


if __name__ == "__main__":
    main()
