"""AWS authentication utilities."""
from __future__ import annotations

import datetime
import logging
import os

import botocore.session

logger = logging.getLogger(__name__)


def generate_mfa_code(string_seed: str) -> str:
    """Generate a 6-digit TOTP MFA code from a base32-encoded seed."""
    import pyotp

    totp = pyotp.TOTP(string_seed)
    return totp.generate_otp(totp.timecode(datetime.datetime.now()))


def get_mfa_credentials(mfa_serial: str, token_code: str, region: str, profile: str | None = None) -> None:
    """Call STS GetSessionToken with MFA and export temp credentials to env."""
    import boto3

    session = boto3.Session(profile_name=profile, region_name=region)
    sts = session.client("sts")
    resp = sts.get_session_token(
        SerialNumber=mfa_serial,
        TokenCode=token_code,
    )
    creds = resp["Credentials"]
    os.environ["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    os.environ["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    logger.info("MFA temporary credentials acquired successfully, valid until %s", creds["Expiration"])


def setup_aws_session(
    profile: str | None = None,
    region_override: str | None = None,
    default_region: str = "us-east-1",
) -> str:
    """Read AWS profile config, handle MFA if needed, return resolved region.

    Determines how to obtain the MFA code based on where the MFA serial is configured:

    - Environment variable ``AWS_MFA_SERIAL``: auto-generate MFA code via TOTP; requires ``AWS_MFA_SEED``.
    - Profile config ``mfa_serial``: interactively prompt the user for a 6-digit code.

    The two MFA serials may differ; env takes precedence. If neither is set, MFA is skipped.
    """
    session = botocore.session.Session(profile=profile)
    profile_cfg = session.get_scoped_config()
    aws_region = region_override or default_region or profile_cfg.get("region")

    if env_serial := os.getenv("AWS_MFA_SERIAL"):
        mfa_serial = env_serial
        mfa_seed = os.getenv("AWS_MFA_SEED")
        if not mfa_seed:
            raise ValueError("AWS_MFA_SEED environment variable is missing")
        print("[0/4] Auto-generating MFA code and obtaining temporary credentials ...")
        token_code = generate_mfa_code(mfa_seed)
    elif profile_serial := profile_cfg.get("mfa_serial"):
        mfa_serial = profile_serial
        token_code = input("Enter MFA code (6 digits): ").strip()
        if not token_code:
            raise ValueError("MFA code cannot be empty")
        print("[0/4] Manual MFA input, obtaining temporary credentials ...")
    else:
        return aws_region

    get_mfa_credentials(mfa_serial, token_code, aws_region, profile)
    return aws_region
