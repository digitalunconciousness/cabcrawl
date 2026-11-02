"""Settings, read and checked once at startup (Step 5.6)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.config import ConfigError, load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD = {"SECRET_KEY": "k" * 64, "BASE_URL": "https://tracker.synthetic-arcade.net"}


def env(**changes):
    out = dict(GOOD)
    for k, v in changes.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def test_a_good_environment_loads_with_defaults():
    s = load(GOOD)
    assert s.database_url == "sqlite:///arcade.db"
    assert (s.log_level, s.max_photos_per_record, s.max_storage_mb) == ("INFO", 10, 500)
    assert s.cloud_photos is False and s.easypost_api_key == ""
    assert s.warnings == ()


@pytest.mark.parametrize(("changes", "problem"), [
    ({"SECRET_KEY": None}, "SECRET_KEY is not set"),
    ({"SECRET_KEY": "your-secret-key-change-this-for-production"}, "SECRET_KEY is not set"),
    ({"DATABASE_URL": "not a url at all"}, "DATABASE_URL can't be read"),
    ({"DATABASE_URL": "mysql://u@h/db"}, "only PostgreSQL and SQLite"),
    ({"BASE_URL": "tracker.synthetic-arcade.net"}, "must start with https://"),
    ({"BASE_URL": "https://tracker.synthetic-arcade.net/app"}, "nothing after the host"),
    ({"BASE_URL": "https://tracker.example.com"}, "still the example"),
    ({"SOURCE_URL": "github"}, "SOURCE_URL"),
    ({"LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
    ({"MAX_PHOTOS_PER_RECORD": "ten"}, "whole number"),
    ({"MAX_TOTAL_STORAGE_MB": "0"}, "at least 1"),
    ({"USE_CLOUD_STORAGE": "maybe"}, "true or false"),
])
def test_a_setting_the_app_cannot_run_with_is_refused(changes, problem):
    with pytest.raises(ConfigError, match=problem):
        load(env(**changes))


def test_every_problem_is_reported_at_once():
    """One restart per typo is a long morning."""
    with pytest.raises(ConfigError) as caught:
        load({"BASE_URL": "nope", "LOG_LEVEL": "LOUD"})
    message = str(caught.value)
    assert "SECRET_KEY" in message and "BASE_URL" in message and "LOG_LEVEL" in message


@pytest.mark.parametrize(("changes", "warning"), [
    ({"SECRET_KEY": "short-but-set"}, "only 13 characters"),
    ({"BASE_URL": None}, "BASE_URL is not set"),
    ({"USE_CLOUD_STORAGE": "true", "AWS_ACCESS_KEY_ID": "your-aws-access-key-here"},
     "photos are kept on this server only"),
])
def test_a_degraded_setting_warns_and_runs(changes, warning):
    s = load(env(**changes))
    assert any(warning in w for w in s.warnings), s.warnings


def test_cloud_copies_need_every_credential():
    on = env(USE_CLOUD_STORAGE="true", AWS_ACCESS_KEY_ID="AKIA-SYNTH",
             AWS_SECRET_ACCESS_KEY="synthetic", AWS_BUCKET_NAME="synthetic-bucket")
    assert load(on).cloud_photos is True
    assert load(env(USE_CLOUD_STORAGE="true")).cloud_photos is False


def test_placeholders_from_the_example_count_as_unset():
    assert load(env(EASYPOST_API_KEY="your-easypost-api-key-here")).easypost_api_key == ""


def test_a_trailing_slash_on_base_url_is_harmless():
    assert load(env(BASE_URL="https://tracker.synthetic-arcade.net/")).base_url == \
        "https://tracker.synthetic-arcade.net"


def test_the_app_reads_its_settings_from_here(app):
    assert app.config["MAX_PHOTOS_PER_RECORD"] == 10 and app.config["LOG_LEVEL"] == "INFO"


def test_the_photo_limit_setting_is_used(app, client, tmp_path):
    """MAX_PHOTOS_PER_RECORD was in .env.example and read by nothing."""
    import io

    from PIL import Image

    from app.extensions import db
    from app.models import MaintenanceRecord
    from app.services.work_orders import save_photos

    app.config["MAX_PHOTOS_PER_RECORD"] = 1
    app.config["UPLOAD_FOLDER"] = str(tmp_path)       # keep the photos out of the checkout
    order = MaintenanceRecord(issue_description="Synthetic fault")
    db.session.add(order)
    db.session.commit()

    def png():
        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, "PNG")
        buf.seek(0)
        from werkzeug.datastructures import FileStorage

        return FileStorage(buf, filename="a.png")

    saved, problems = save_photos(order, [png(), png()])
    assert saved == 1 and "at most 1 photos" in problems[0]
    assert os.listdir(tmp_path / "maintenance_photos") == order.get_photos()


def run_check(extra: dict) -> subprocess.CompletedProcess:
    child = {k: v for k, v in os.environ.items()
             if k not in ("SECRET_KEY", "BASE_URL", "DATABASE_URL")}
    child.update(extra)
    return subprocess.run([sys.executable, "scripts/check_config.py"], cwd=ROOT, env=child,
                          capture_output=True, text=True, check=False)


def test_the_check_script_passes_a_good_env_and_prints_no_secret(tmp_path):
    result = run_check({**GOOD, "DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "configuration: OK" in result.stdout
    assert GOOD["SECRET_KEY"] not in result.stdout


def test_the_check_script_fails_a_bad_env():
    result = run_check({"SECRET_KEY": "k" * 64, "BASE_URL": "https://tracker.example.com"})
    assert result.returncode == 1 and "still the example" in result.stdout


def test_deploy_checks_the_config_before_it_migrates():
    with open(os.path.join(ROOT, "deploy", "deploy.sh"), encoding="utf-8") as fh:
        script = fh.read()
    assert script.index("scripts/check_config.py") < script.index("db upgrade")
    assert script.index("scripts/check_config.py") < script.index("systemctl restart")
