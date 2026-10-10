#!/bin/bash
cd "$(dirname "$0")"
# optional overrides:  export DB_USER=... DB_PASSWORD=... DB_NAME=... DB_HOST=...
python3 app.py
