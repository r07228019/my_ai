# NBA 每日戰報產生器

一支命令列 Python 程式，用來自動擷取當日 NBA 比賽結果與球員數據，並透過 **Claude Sonnet 4.6** 彙整成一份繁體中文的 Markdown 戰報。

## 功能說明

執行後會依序完成：

1. **抓資料**：呼叫 [`nba_api`](https://github.com/swar/nba_api) 的 Live Scoreboard 取得「美東時間當日」的所有比賽；對已完賽的場次再呼叫 Boxscore 取得球員個人數據（得分、籃板、助攻、抄截、阻攻、失誤、投籃命中率、正負值等）。
2. **彙整報告**：把結構化資料丟給 Claude Sonnet 4.6，由模型依照固定格式撰寫戰報。
3. **寫檔**：輸出到 `report/nba_daily_report_YYYY-MM-DD.md`（檔名中的日期為美東時間），`report/` 目錄會自動建立。

報告固定包含五個段落：

- 當日戰況總覽
- 值得注意的看點
- 關鍵數據
- 特殊事件
- 重要人事異動（若 API 無相關資訊，會明確註明）

## 專案結構

```
nba_daily_report/
├── main.py              # 主程式入口
├── requirements.txt     # Python 依賴
├── README.md            # 本文件
└── report/              # 產出的每日戰報 (.md) 存放處
```

## 安裝

需要 Python 3.9+（使用了 `zoneinfo`）。

本專案的依賴安裝在虛擬環境 `my_ai_venv` 中，執行前請先啟用：

```bash
# 啟用虛擬環境
source my_ai_venv/bin/activate

# 首次安裝依賴（已安裝過可跳過）
cd test/nba_daily_report
pip install -r requirements.txt
```

## 設定

透過 **AWS Bedrock** 呼叫 Claude，需要設定 AWS 憑證。

### 基本 AWS 憑證

先確認 AWS CLI 已設定好 IAM Access Key：

```bash
aws configure
# AWS Access Key ID: ...
# AWS Secret Access Key: ...
# Default region name: us-east-1
```

### MFA 認證（組織 SCP 要求 MFA）

如果組織的 SCP 規則要求 MFA 才能操作，設定 MFA 裝置的 ARN：

```bash
export AWS_MFA_SERIAL=arn:aws:iam::393326654921:mfa/YOUR_IAM_USER
```

設定後，程式執行時會提示你輸入 6 碼 MFA 驗證碼，自動透過 STS 取得臨時憑證（預設有效 12 小時）。

若沒設 `AWS_MFA_SERIAL`，程式會直接使用現有的 AWS credential chain（適合已經有 MFA session 或不需要 MFA 的環境）。

## 使用

確認已啟用虛擬環境（提示符前方應顯示 `(my_ai_venv)`），再執行：

```bash
python main.py --profile cathay-dt-lab --region us-east-1
```

執行範例輸出（有 MFA）：

```
請輸入 MFA 驗證碼 (6 碼): 123456
[0/3] 取得 MFA 臨時憑證 ...
      MFA 臨時憑證取得成功，有效至 2026-04-18 12:00:00+00:00
[1/3] 擷取 2026-04-18 (ET) 的 NBA 比賽資料 ...
      找到 8 場比賽
[2/3] 呼叫 Claude (us.anthropic.claude-sonnet-4-6) 彙整報告 ...
[3/3] 已寫入：.../nba_daily_report_2026-04-18.md
```

完成後打開 `report/nba_daily_report_2026-04-18.md` 即可閱讀戰報。

## 設計備註

- **時區**：NBA 賽程以美東時間 (US/Eastern) 為基準，因此「當日」以 ET 為準，而非台灣時區。
- **資料來源**：只使用官方 Live 端點；進行中或未開賽的比賽僅帶出比分與隊伍資訊，不拉 box score。
- **模型選擇**：透過 AWS Bedrock 使用 `us.anthropic.claude-sonnet-4-6`，呼叫時採用 streaming 以避免長輸出 timeout。
- **MFA 支援**：設定 `AWS_MFA_SERIAL` 環境變數後，程式會互動式詢問 MFA 驗證碼，透過 STS `GetSessionToken` 取得臨時憑證，符合 SCP 的 MFA 要求。
- **人事異動資料**：NBA API 並不提供交易、簽約、教練異動等資訊，因此系統提示要求模型在資料缺乏時明確註明，不可虛構。
- **無賽事處理**：若當日沒有任何比賽，跳過 API 呼叫，直接輸出簡短說明。

## 已知限制

- `nba_api` 走的是非官方封裝，若 NBA 端點或回傳格式變動，可能需要更新套件版本。
- 若在賽事進行中執行，尚未完賽的比賽不會有球員數據；建議在美東時間深夜 / 隔日凌晨執行以取得完整戰報。
- 報告內容由 LLM 生成，仍可能有誤讀或遺漏，請作為參考而非權威資料。
