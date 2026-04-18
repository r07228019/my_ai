# utils/

專案共用工具模組，目前僅包含 AWS 認證相關邏輯。

## 檔案結構

```
utils/
├── __init__.py
├── aws_auth.py        # AWS MFA 認證工具
└── README.md
```

## 背景：組織 SCP 政策

本組織於 AWS 帳號層級套用 SCP（Service Control Policy），**要求所有 IAM 使用者的操作都必須通過 MFA 驗證**，否則 STS 以外的呼叫會直接被拒絕。因此任何需要存取 AWS 資源的程式（例如呼叫 Bedrock、S3、SNS ...）都必須先完成以下流程：

1. 使用長期 access key 呼叫 `sts:GetSessionToken`，並附上當下的 MFA 6 碼驗證碼。
2. 拿到的臨時憑證（access key / secret / session token）才擁有完整操作權限。
3. 後續所有 AWS client 以此臨時憑證為準。

`aws_auth.py` 封裝的就是第 1、2 步，讓呼叫端不必重複實作。

## 為什麼要同時支援「自動產生」與「手動輸入」兩種 MFA 流程？

本組織的 IAM user 在某些情境下會擁有**兩個不同的 MFA 裝置**：

| MFA 裝置 | 用途 | 取碼方式 |
|---|---|---|
| 日常自動化用（低風險操作） | 排程任務、自動化腳本 | 以 base32 seed 透過 TOTP 自動產生 |
| 高權限操作用（需人為把關） | 建立資源、修改設定 | 需使用者實際輸入 6 碼 |

為了兼顧兩種情境，`setup_aws_session()` 依 **MFA serial ARN 的設定來源** 決定走哪條路徑：

| MFA serial 來源 | 行為 | 適用情境 |
|---|---|---|
| 環境變數 `AWS_MFA_SERIAL` | 自動以 TOTP 產生 MFA code（需 `AWS_MFA_SEED`） | 日常自動化 |
| `~/.aws/config` 的 `mfa_serial` | 互動式要求輸入 6 碼 | 需人為確認的操作 |
| 兩者皆未設定 | 跳過 MFA，走 AWS 預設 credential chain | 已有既存 session 或不需 MFA 的環境 |

**優先順序**：環境變數 > profile config。env 有設定時一律走自動產生；若 env 有 serial 但缺 seed 則直接報錯，不會 fallback 為互動輸入。

## 模組 API：`aws_auth.py`

### `setup_aws_session(profile, region_override, default_region) -> str`

主要入口，處理整個 MFA 認證流程並回傳最終使用的 AWS region。

**參數**
- `profile` (`str | None`)：AWS profile 名稱（對應 `~/.aws/credentials` / `~/.aws/config`）。
- `region_override` (`str | None`)：若指定則覆蓋 profile 中的 region。
- `default_region` (`str`)：兩者皆未設定時的 fallback，預設 `us-east-1`。

**副作用**
- 成功時會將臨時憑證寫入當前 process 的環境變數：
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_SESSION_TOKEN`
- 只影響當前 Python process（不會污染 shell rc 檔）；process 結束即消失。

**例外**
- `ValueError`：MFA code 為空、或 env 設了 serial 但缺 seed。

### `generate_mfa_code(string_seed) -> str`

以 base32 seed 產生當下時間的 6 碼 TOTP，供自動流程使用。

### `get_mfa_credentials(mfa_serial, token_code, region, profile) -> None`

實際呼叫 STS `GetSessionToken` 並將臨時憑證寫入環境變數。一般情境下不需直接呼叫，由 `setup_aws_session()` 內部使用。

## 環境變數

| 變數 | 用途 | 必要性 |
|---|---|---|
| `AWS_MFA_SERIAL` | 自動流程的 MFA 裝置 ARN | 啟用自動流程時必填 |
| `AWS_MFA_SEED` | 自動流程的 base32 TOTP seed | 搭配 `AWS_MFA_SERIAL` 必填 |

建議寫在 `~/.zshrc`（macOS 預設 shell 是 zsh，`~/.bashrc` 不會被載入）：

```bash
export AWS_MFA_SERIAL=arn:aws:iam::123456789012:mfa/YOUR_AUTO_DEVICE
export AWS_MFA_SEED=YOUR_BASE32_SEED
```

> ⚠️ `AWS_MFA_SEED` 等同於實體 MFA 裝置本身，請確保 rc 檔權限為 `600`，並務必避免 commit 到 git。

## 呼叫端範例

```python
from utils.aws_auth import setup_aws_session

# 走 profile config 中的 default region
aws_region = setup_aws_session(profile="cathay-dt-lab")

# 覆蓋 region
aws_region = setup_aws_session(
    profile="cathay-dt-lab",
    region_override="us-east-1",
    default_region="ap-southeast-1",
)

# 之後下游建立 client，憑證會從環境變數自動讀取
import boto3
s3 = boto3.client("s3", region_name=aws_region)
```

實際使用範例可參考 [test/nba_daily_report/main.py](../test/nba_daily_report/main.py)。

## 相依套件

- `boto3`、`botocore`：AWS SDK。
- `pyotp`：TOTP 產生（僅自動流程需要）。

請確保專案根目錄的 [requirements.txt](../requirements.txt) 已安裝上述套件。
