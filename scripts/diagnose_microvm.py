"""Diagnose connectivity to a candidate Lambda MicroVM.

Run from the backend/ directory with an active interview ID:

    python scripts/diagnose_microvm.py d4462a67-f00e-44d9-8d0e-556045326d02

This prints the MicroVM connection details and makes the same HTTP request the
backend's ide_proxy makes, so you can see exactly what the upstream returns.
"""

import asyncio
import sys
from pathlib import Path

# Make src/ importable when running from backend/scripts/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from src.database import AsyncSessionLocal
from src.lib import microvm_manager
from src.repositories.interview_repository import interview_repository


async def diagnose(interview_id: str) -> None:
    async with AsyncSessionLocal() as db:
        interview = await interview_repository.get(db, interview_id)
        if interview is None:
            print(f"Interview not found: {interview_id}")
            return

        print(f"Interview ID: {interview.id}")
        print(f"Status: {interview.status}")
        print(f"MicroVM ID: {interview.microvm_id}")
        print(f"MicroVM endpoint: {interview.microvm_endpoint}")
        print(
            f"Auth token: {interview.auth_token[:20] + '...' if interview.auth_token else 'None'}"
        )
        print(f"Token expires at: {interview.token_expires_at}")

        if not interview.microvm_endpoint or not interview.auth_token:
            print("Interview has no MicroVM connection details.")
            return

        endpoint = interview.microvm_endpoint.strip()
        if not endpoint.startswith(("http://", "https://")):
            endpoint = f"https://{endpoint}"

        paths = ["/", "/healthz", "/health"]
        headers_minimal = {
            "X-aws-proxy-auth": interview.auth_token,
            "X-aws-proxy-port": str(microvm_manager.TARGET_PORT),
        }

        for path in paths:
            url = endpoint.rstrip("/") + path
            await _do_get(url, headers_minimal)

        # code-server redirects / to /?folder=/home/coder/workspace.
        # Test the redirect target because that is what the browser ends up loading.
        redirect_url = endpoint.rstrip("/") + "/?folder=/home/coder/workspace"
        print("\n--- Testing code-server redirect target ---")
        await _do_get(redirect_url, headers_minimal)

        # Mimic exactly what the backend's ide_proxy.py does (build_request + stream).
        print("\n--- Mimicking backend ide_proxy request to / ---")
        url = endpoint.rstrip("/") + "/"
        print(f"URL: {url}")
        try:
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
                http1=True,
                http2=False,
            ) as client:
                req = client.build_request(
                    method="GET",
                    url=url,
                    headers=headers_minimal,
                    content=b"",
                )
                response = await client.send(req, stream=True)
            print(f"Status: {response.status_code}")
            print(f"Headers: {dict(response.headers)}")
            body = await response.aread()
            print(f"Body (first 1000 chars): {body[:1000].decode('utf-8', errors='replace')}")
        except Exception as exc:
            print(f"Request failed: {exc}")


async def _do_get(url: str, headers: dict) -> None:
    print(f"\n--- GET {url} ---")
    print(f"Headers: {headers}")
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
            http1=True,
            http2=False,
        ) as client:
            resp = await client.get(url, headers=headers)
        print(f"Status: {resp.status_code}")
        print(f"Headers: {dict(resp.headers)}")
        print(f"Body (first 1000 chars): {resp.text[:1000]}")
    except Exception as exc:
        print(f"Request failed: {exc}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/diagnose_microvm.py <interview_id>")
        sys.exit(1)
    asyncio.run(diagnose(sys.argv[1]))
