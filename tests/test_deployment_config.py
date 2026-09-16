from scripts.check_deployment import REQUIRED, validate


def settings():
    values = dict.fromkeys(REQUIRED, "configured")
    values.update(DATABASE_URL="postgresql://postgres.ref:password@aws-0-region.pooler.supabase.com:6543/postgres?sslmode=require",
        VITE_API_URL="https://brief.onrender.com", VITE_SUPABASE_URL="https://ref.supabase.co",
        VITE_SUPABASE_PUBLISHABLE_KEY="sb_publishable_test", RENDER_SERVICE_ID="srv-test",
        CLOUDFLARE_PROJECT_NAME="world-brief")
    return values


def test_complete_deployment_inputs():
    assert validate(settings()) == []


def test_missing_inputs_report_names_not_values():
    errors = validate({})
    assert len(errors) == len(REQUIRED)
    assert all(error.startswith("Missing ") for error in errors)


def test_reject_insecure_database_and_direct_ipv6():
    values = settings()
    values['DATABASE_URL'] = "postgresql://postgres:secret@db.ref.supabase.co:5432/postgres"
    errors = validate(values)
    assert any("TLS" in error for error in errors)
    assert any("IPv6" in error for error in errors)
    assert all("secret" not in error for error in errors)


def test_reject_backend_credentials_in_browser_configuration():
    values = settings()
    values['VITE_SUPABASE_PUBLISHABLE_KEY'] = 'sb_secret_private'
    values['VITE_API_URL'] = 'https://user:password@example.com/api'
    errors = validate(values)
    assert len(errors) == 2
    assert all('sb_secret_private' not in error and 'user:password' not in error for error in errors)
