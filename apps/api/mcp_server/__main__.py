from apps.api.mcp_server.bootstrap import setup_django


def main() -> None:
    setup_django()

    from apps.api.mcp_server.server import create_mcp_server

    create_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
