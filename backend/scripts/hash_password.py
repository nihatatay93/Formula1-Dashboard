"""Generate the value for ``DASHBOARD_PASSWORD_HASH``.

    python -m scripts.hash_password

The password is read from a prompt rather than an argument, so it does not
land in shell history or in the process list. Nothing is written to disk: copy
the printed hash into the deployment's secret store.
"""

from __future__ import annotations

import getpass
import sys

from app.auth.policy import AuthConfigurationError, hash_password


def main() -> int:
    try:
        password = getpass.getpass("Dashboard password: ")
        again = getpass.getpass("Repeat password: ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 1

    if password != again:
        print("Those passwords do not match.", file=sys.stderr)
        return 1

    try:
        encoded = hash_password(password)
    except AuthConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1

    print("\nSet these in your deployment's secret store:\n")
    print(f"DASHBOARD_PASSWORD_HASH={encoded}")
    print("DASHBOARD_SECRET_KEY=<48+ random characters, e.g. openssl rand -hex 32>")
    print(
        "\nRotating DASHBOARD_SECRET_KEY signs out every browser and "
        "invalidates every issued client token."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
