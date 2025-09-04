import os

from keycardai.oauth import Client


def main():
    with Client(os.getenv("ZONE_URL")) as client:
        access_token = client.get_access_token_for_resource(
            "https://www.googleapis.com/calendar/v3",
            "user_access_token"
        )
        print(f"Access Token: {access_token}")
        return access_token


if __name__ == "__main__":
    main()
