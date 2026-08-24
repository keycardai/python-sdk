"""Framework-agnostic web-app authorization-code flow example."""

from collections.abc import Mapping

from keycardai.oauth.pkce import (
    AuthorizationRedirect,
    begin_authorization,
    complete_authorization,
)
from keycardai.oauth.types.models import TokenResponse

session: dict[str, dict[str, str]] = {}


async def login_redirect() -> AuthorizationRedirect:
    """Start login and store the flow values in the user's session."""
    redirect = await begin_authorization(
        client_id="my-web-app",
        issuer="https://oauth.example.com",
        redirect_uri="https://app.example.com/oauth/callback",
        scopes=["openid", "profile"],
    )
    session["oauth_flow"] = {
        "state": redirect.state,
        "code_verifier": redirect.code_verifier,
    }
    return redirect


async def oauth_callback(callback_params: Mapping[str, str]) -> TokenResponse:
    """Exchange the callback after retrieving the flow values from the session."""
    flow = session.pop("oauth_flow")
    return await complete_authorization(
        callback_params=callback_params,
        state=flow["state"],
        code_verifier=flow["code_verifier"],
        client_id="my-web-app",
        issuer="https://oauth.example.com",
        redirect_uri="https://app.example.com/oauth/callback",
    )


def main() -> None:
    """Show where a web framework would connect the two route handlers."""
    print("Connect login_redirect() and oauth_callback() to your web routes.")


if __name__ == "__main__":
    main()
