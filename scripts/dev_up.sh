#!/usr/bin/env bash
set -euo pipefail

export FLASK_APP="popsicle.webhook.app:app"
poetry run flask run --reload

