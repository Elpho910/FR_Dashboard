#!/usr/bin/env python3
"""Provision trusted client credentials for Raspberry Pi display devices."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys

from dotenv import load_dotenv

from fr24_reporter.store import get_trusted_client, init_db, upsert_trusted_client

DEFAULT_SERVER_URL = "https://your-server-url"


def slugify_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "display"


def build_unique_client_id(display_name: str) -> str:
    base_id = f"client-{slugify_name(display_name)}"
    candidate = base_id
    suffix = 2

    while get_trusted_client(candidate) is not None:
        candidate = f"{base_id}-{suffix}"
        suffix += 1

    return candidate


def add_client(display_name: str, explicit_client_id: str | None) -> int:
    normalized_name = display_name.strip()
    if not normalized_name:
        print("Display name is required.", file=sys.stderr)
        return 1

    client_id = explicit_client_id.strip() if explicit_client_id else build_unique_client_id(normalized_name)
    if explicit_client_id and get_trusted_client(client_id) is not None:
        print(f"Client ID '{client_id}' already exists.", file=sys.stderr)
        return 1

    client_secret = secrets.token_urlsafe(32)
    upsert_trusted_client(client_id, client_secret, client_name=normalized_name, enabled=True)

    server_base_url = (os.getenv("SERVER_BASE_URL", "") or "").strip() or DEFAULT_SERVER_URL

    print("Trusted client created.")
    print(f"Name: {normalized_name}")
    print(f"Client ID: {client_id}")
    print(f"Client secret: {client_secret}")
    print("")
    print("Paste this into the client .env:")
    print("APP_ROLE=client")
    print(f"SERVER_BASE_URL={server_base_url}")
    print(f"CLIENT_ID={client_id}")
    print(f"CLIENT_SECRET={client_secret}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a trusted Raspberry Pi client credential for the dashboard server."
    )
    parser.add_argument("display_name", help="Human-friendly name, for example 'Burnie Main Screen'")
    parser.add_argument(
        "--client-id",
        help="Optional explicit client ID. If omitted, one is generated from the display name.",
    )
    return parser


def main() -> int:
    load_dotenv()
    init_db()
    parser = build_parser()
    args = parser.parse_args()
    return add_client(args.display_name, args.client_id)


if __name__ == "__main__":
    raise SystemExit(main())
