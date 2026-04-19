# utils/

Shared utility modules for the project. Currently contains only AWS authentication logic.

## File structure

```
utils/
├── __init__.py
├── aws_auth.py        # AWS MFA authentication utility
└── README.md
```

## Background: organizational SCP policy

This organization applies an SCP (Service Control Policy) at the AWS account level that **requires all IAM user operations to pass MFA verification**; otherwise, calls other than STS are rejected outright. Any program that needs to access AWS resources (e.g., calling Bedrock, S3, SNS, ...) must therefore first complete the following flow:

1. Use a long-lived access key to call `sts:GetSessionToken`, along with the current 6-digit MFA code.
2. The returned temporary credentials (access key / secret / session token) have full operation privileges.
3. All subsequent AWS clients rely on these temporary credentials.

`aws_auth.py` encapsulates steps 1 and 2 so callers don't have to reimplement them.

## Why support both "auto-generate" and "manual input" MFA flows?

IAM users in this organization may own **two different MFA devices** for different scenarios:

| MFA device | Purpose | How to obtain code |
|---|---|---|
| Daily automation (low-risk operations) | Scheduled tasks, automation scripts | Auto-generated via TOTP from a base32 seed |
| High-privilege operations (requires human gatekeeping) | Resource creation, configuration changes | Requires the user to enter 6 digits manually |

To accommodate both scenarios, `setup_aws_session()` chooses the path based on **where the MFA serial ARN is configured**:

| MFA serial source | Behavior | Applicable scenario |
|---|---|---|
| Environment variable `AWS_MFA_SERIAL` | Auto-generate MFA code via TOTP (requires `AWS_MFA_SEED`) | Daily automation |
| `mfa_serial` in `~/.aws/config` | Interactively prompt for 6-digit code | Operations requiring human confirmation |
| Neither is set | Skip MFA, use AWS default credential chain | Environments with an existing session or that don't need MFA |

**Precedence:** environment variable > profile config. When the env var is set, the auto-generate path is always used; if env has the serial but is missing the seed, an error is raised — there is no fallback to interactive input.

## Module API: `aws_auth.py`

### `setup_aws_session(profile, region_override, default_region) -> str`

Main entry point. Handles the entire MFA authentication flow and returns the final AWS region to use.

**Parameters**
- `profile` (`str | None`): AWS profile name (matching `~/.aws/credentials` / `~/.aws/config`).
- `region_override` (`str | None`): If specified, overrides the region in the profile.
- `default_region` (`str`): Fallback when neither of the above is set. Defaults to `us-east-1`.

**Side effects**
- On success, writes temporary credentials to the current process's environment variables:
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_SESSION_TOKEN`
- Only affects the current Python process (does not pollute shell rc files); credentials disappear when the process exits.

**Exceptions**
- `ValueError`: MFA code is empty, or env has the serial set but is missing the seed.

### `generate_mfa_code(string_seed) -> str`

Generates a 6-digit TOTP for the current time from a base32 seed, used by the automated flow.

### `get_mfa_credentials(mfa_serial, token_code, region, profile) -> None`

Actually calls STS `GetSessionToken` and writes temporary credentials to environment variables. Not typically called directly — used internally by `setup_aws_session()`.

## Environment variables

| Variable | Purpose | Required |
|---|---|---|
| `AWS_MFA_SERIAL` | MFA device ARN for the automated flow | Required when enabling the automated flow |
| `AWS_MFA_SEED` | base32 TOTP seed for the automated flow | Required together with `AWS_MFA_SERIAL` |

Recommended to put these in `~/.zshrc` (macOS's default shell is zsh; `~/.bashrc` is not loaded):

```bash
export AWS_MFA_SERIAL=arn:aws:iam::123456789012:mfa/YOUR_AUTO_DEVICE
export AWS_MFA_SEED=YOUR_BASE32_SEED
```

> ⚠️ `AWS_MFA_SEED` is equivalent to the physical MFA device itself. Make sure the rc file permissions are `600`, and never commit it to git.

## Caller example

```python
from utils.aws_auth import setup_aws_session

# Use the default region from the profile config
aws_region = setup_aws_session(profile="cathay-dt-lab")

# Override region
aws_region = setup_aws_session(
    profile="cathay-dt-lab",
    region_override="us-east-1",
    default_region="ap-southeast-1",
)

# Downstream clients will read credentials from env vars automatically
import boto3
s3 = boto3.client("s3", region_name=aws_region)
```

For a real usage example, see [test/nba_daily_report/main.py](../test/nba_daily_report/main.py).

## Dependencies

- `boto3`, `botocore`: AWS SDK.
- `pyotp`: TOTP generation (only needed for the automated flow).

Make sure the above packages are installed via [requirements.txt](../requirements.txt) at the project root.
