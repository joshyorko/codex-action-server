"""Shared helpers for native app-server protocol fixtures."""


def native_server_user_agent(
    build_version: str,
    *,
    originator: str = "friday-external-codex",
    client_name: str = "friday-external-codex",
    client_version: str = "0.1.0",
) -> str:
    """Format the pinned app-server InitializeResponse userAgent shape."""
    return (
        f"{originator}/{build_version} (Linux Unknown; x86_64) unknown "
        f"({client_name}; {client_version})"
    )
