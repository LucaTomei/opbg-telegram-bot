"""Generate the cryptographic material required by the application.

Run once per environment and store the output in the environment file or in
the secret manager.  Keys are never written to the repository.
"""

from __future__ import annotations

import argparse
import base64
import secrets


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate application secrets")
    parser.add_argument(
        "--format",
        choices=("env", "plain"),
        default="env",
        help="output as environment assignments or as bare values",
    )
    args = parser.parse_args()

    values = {
        "APP_SECRET_KEY": _key(),
        "APP_ENCRYPTION_KEY": _key(),
        "APP_BLIND_INDEX_KEY": _key(),
        "MYSQL_PASSWORD": _key(),
        "MYSQL_ROOT_PASSWORD": _key(),
    }
    for name, value in values.items():
        print(f"{name}={value}" if args.format == "env" else value)  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
