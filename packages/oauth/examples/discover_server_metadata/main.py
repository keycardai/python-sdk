import os

from keycardai.oauth.client import Client


def main():
    print("Hello from discover-server-metadata!")
    with Client(os.getenv("ZONE_URL")) as client:
        metadata = client.discover_server_metadata()
        print(metadata)


if __name__ == "__main__":
    main()
