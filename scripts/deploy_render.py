"""Deploy the tested commit. Refuse a paid Render service and wait for completion."""
import os
import time
import httpx


def main():
    token = os.environ.get("RENDER_API_KEY")
    service = os.environ.get("RENDER_SERVICE_ID")
    commit = os.environ.get("GITHUB_SHA")
    if not all((token, service, commit)):
        raise SystemExit("Configure RENDER_API_KEY and RENDER_SERVICE_ID in GitHub.")
    with httpx.Client(base_url="https://api.render.com/v1", headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        response = client.get(f"/services/{service}")
        response.raise_for_status()
        if response.json().get("serviceDetails", {}).get("plan") != "free":
            raise SystemExit("Refusing deployment: the Render service is not on the free plan.")
        response = client.post(f"/services/{service}/deploys", json={"commitId": commit})
        response.raise_for_status()
        deploy_id = response.json()["id"]
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            response = client.get(f"/services/{service}/deploys/{deploy_id}")
            response.raise_for_status()
            status = response.json()["status"]
            if status == "live":
                print(f"Backend commit {commit[:12]} is live.")
                return
            if status in ("build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"):
                raise SystemExit(f"Render deployment ended: {status}")
            time.sleep(15)
        raise SystemExit("Deployment timed out; check Render before retrying.")


if __name__ == "__main__":
    main()
