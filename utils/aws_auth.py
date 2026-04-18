"""AWS authentication utilities."""
from __future__ import annotations

import logging
import os

import botocore.session

logger = logging.getLogger(__name__)


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
    logger.info("MFA 臨時憑證取得成功，有效至 %s", creds["Expiration"])


def setup_aws_session(
    profile: str | None = None,
    region_override: str | None = None,
    default_region: str = "us-east-1",
) -> str:
    """Read AWS profile config, handle MFA if needed, return resolved region."""
    session = botocore.session.Session(profile=profile)
    profile_cfg = session.get_scoped_config()
    aws_region = region_override or default_region or profile_cfg.get("region")
    mfa_serial = profile_cfg.get("mfa_serial") or os.getenv("AWS_MFA_SERIAL")

    if mfa_serial:
        token_code = input("請輸入 MFA 驗證碼 (6 碼): ").strip()
        if not token_code:
            raise ValueError("MFA 驗證碼不可為空")
        print("[0/3] 取得 MFA 臨時憑證 ...")
        get_mfa_credentials(mfa_serial, token_code, aws_region, profile)

    return aws_region
