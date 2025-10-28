#!/usr/bin/env bash
set -euo pipefail

export FLASK_APP="fmg.webhook.app:app"
poetry run flask run --reload
