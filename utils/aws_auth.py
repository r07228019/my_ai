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
    logger.info("MFA 臨時憑證取得成功，有效至 %s", creds["Expiration"])


def setup_aws_session(
    profile: str | None = None,
    region_override: str | None = None,
    default_region: str = "us-east-1",
) -> str:
    """Read AWS profile config, handle MFA if needed, return resolved region.

    根據 MFA serial 的來源，決定如何取得 MFA code：

    - 環境變數 ``AWS_MFA_SERIAL``：以 TOTP 自動產生 MFA code，需搭配 ``AWS_MFA_SEED``。
    - profile config 的 ``mfa_serial``：互動式要求使用者輸入 6 碼驗證碼。

    兩組 MFA serial 可以不同；env 優先，兩者皆未設定則跳過 MFA。
    """
    session = botocore.session.Session(profile=profile)
    profile_cfg = session.get_scoped_config()
    aws_region = region_override or default_region or profile_cfg.get("region")

    if env_serial := os.getenv("AWS_MFA_SERIAL"):
        mfa_serial = env_serial
        mfa_seed = os.getenv("AWS_MFA_SEED")
        if not mfa_seed:
            raise ValueError("環境變數缺少 AWS_MFA_SEED，請確認")
        print("[0/4] 自動產生 MFA code 並取得臨時憑證 ...")
        token_code = generate_mfa_code(mfa_seed)
    elif profile_serial := profile_cfg.get("mfa_serial"):
        mfa_serial = profile_serial
        token_code = input("請輸入 MFA 驗證碼 (6 碼): ").strip()
        if not token_code:
            raise ValueError("MFA 驗證碼不可為空")
        print("[0/4] 手動輸入 MFA，取得臨時憑證 ...")
    else:
        return aws_region

    get_mfa_credentials(mfa_serial, token_code, aws_region, profile)
    return aws_region
