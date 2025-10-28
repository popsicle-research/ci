#!/usr/bin/env bash
set -euo pipefail

poetry run ruff format
poetry run ruff check --fix
