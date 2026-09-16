"""Validate deployment inputs before changing any cloud resources. Never print values."""
import os
import re
from urllib.parse import urlsplit, parse_qs

REQUIRED = (
    "DATABASE_URL", "GROQ_API_KEY", "RENDER_API_KEY", "RENDER_SERVICE_ID",
    "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_PROJECT_NAME",
    "VITE_API_URL", "VITE_SUPABASE_URL", "VITE_SUPABASE_PUBLISHABLE_KEY",
)


def validate(values):
    errors = [f"Missing {name}" for name in REQUIRED if not values.get(name, "").strip()]
    for name in ("VITE_API_URL", "VITE_SUPABASE_URL"):
        value = values.get(name, "")
        if not value:
            continue
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            errors.append(f"{name} must be an HTTPS origin without credentials, path, or query")
    key = values.get("VITE_SUPABASE_PUBLISHABLE_KEY", "")
    if key and not key.startswith("sb_publishable_"):
        errors.append("VITE_SUPABASE_PUBLISHABLE_KEY must be a Supabase publishable key")
    for name in ("CLOUDFLARE_PROJECT_NAME", "RENDER_SERVICE_ID"):
        value = values.get(name, "")
        if value and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]*", value):
            errors.append(f"Invalid {name}")
    database = values.get("DATABASE_URL", "")
    if database:
        try:
            parsed = urlsplit(database)
            if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname or not parsed.password:
                errors.append("DATABASE_URL must include a PostgreSQL host and password")
            if parse_qs(parsed.query).get("sslmode", [""])[0] not in ("require", "verify-ca", "verify-full"):
                errors.append("DATABASE_URL must require TLS using sslmode")
            if (parsed.hostname or "").startswith("db.") and (parsed.hostname or "").endswith(".supabase.co"):
                errors.append("DATABASE_URL uses Supabase direct IPv6; use the dashboard's IPv4 pooler URL for GitHub Actions")
        except ValueError:
            errors.append("DATABASE_URL is malformed")
    return errors


def main():
    errors = validate(os.environ)
    if errors:
        raise SystemExit("Deployment configuration incomplete:\n- " + "\n- ".join(errors))
    print("Deployment input validation passed.")


if __name__ == "__main__":
    main()
