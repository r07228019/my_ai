"""Fetch today's NBA games, summarize with Claude Sonnet via AWS Bedrock, save as Markdown."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logging.basicConfig(format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import anthropic
from nba_api.live.nba.endpoints import boxscore, scoreboard

DEFAULT_REGION = "us-east-1"
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6"
OUTPUT_DIR = Path(__file__).parent / "report"

SYSTEM_PROMPT = """你是一位資深 NBA 球評與數據分析師，擅長用清晰、具洞察力的繁體中文撰寫每日戰報。

請根據使用者提供的當日 NBA 比賽資料 (JSON)，撰寫一份結構化的 Markdown 報告，內容必須包含以下五個段落 (使用 Markdown 二級標題 `##`)：

1. **當日戰況總覽** — 用 1~2 段文字概述當天比賽數量、重大賽果、勝負亮點。
2. **值得注意的看點** — 條列 3~6 點，說明值得球迷關注的比賽、對位、趨勢。
3. **關鍵數據** — 條列球員或球隊層級的亮眼數據 (大三元、單場高分、罕見紀錄等)，引用具體數字。
4. **特殊事件** — 逆轉、加時、絕殺、里程碑、爭議判決等。若資料中不明顯，說明「暫無特別事件」。
5. **重要人事異動** — 若資料中有顯示交易、簽約、教練異動或傷兵名單異動，彙整於此；若無相關資訊，請註明「本日資料未包含人事異動資訊」。

寫作原則：
- 語氣專業但不枯燥，適度使用球迷語彙。
- 引用數據要具體 (例如「Doncic 35 分 12 助攻 10 籃板完成大三元」)。
- 不要杜撰資料中沒有的資訊。
- 如果當日沒有任何比賽，就簡短說明，不需填充內容。
"""


def fetch_todays_games() -> list[dict]:
    sb = scoreboard.ScoreBoard()
    return sb.get_dict().get("scoreboard", {}).get("games", [])


def fetch_boxscore(game_id: str) -> dict:
    bs = boxscore.BoxScore(game_id=game_id)
    return bs.get_dict().get("game", {})


def _extract_player_stats(team_box: dict) -> list[dict]:
    players = []
    for p in team_box.get("players", []) or []:
        stats = p.get("statistics") or {}
        if not p.get("played", "0") or p.get("played") == "0":
            continue
        players.append({
            "name": p.get("name"),
            "position": p.get("position") or "",
            "starter": p.get("starter", "0") == "1",
            "minutes": stats.get("minutesCalculated") or stats.get("minutes", ""),
            "points": stats.get("points"),
            "rebounds": stats.get("reboundsTotal"),
            "assists": stats.get("assists"),
            "steals": stats.get("steals"),
            "blocks": stats.get("blocks"),
            "turnovers": stats.get("turnovers"),
            "fg": f"{stats.get('fieldGoalsMade')}/{stats.get('fieldGoalsAttempted')}",
            "3pt": f"{stats.get('threePointersMade')}/{stats.get('threePointersAttempted')}",
            "ft": f"{stats.get('freeThrowsMade')}/{stats.get('freeThrowsAttempted')}",
            "plusMinus": stats.get("plusMinusPoints"),
        })
    return players


def build_games_payload(games: list[dict]) -> list[dict]:
    payload = []
    for g in games:
        game_id = g.get("gameId")
        home = g.get("homeTeam", {})
        away = g.get("awayTeam", {})
        status = g.get("gameStatusText", "")
        entry = {
            "gameId": game_id,
            "status": status,
            "statusCode": g.get("gameStatus"),
            "arena": (g.get("arena") or {}).get("arenaName"),
            "home": {
                "team": f"{home.get('teamCity','')} {home.get('teamName','')}".strip(),
                "tricode": home.get("teamTricode"),
                "score": home.get("score"),
                "wins": home.get("wins"),
                "losses": home.get("losses"),
            },
            "away": {
                "team": f"{away.get('teamCity','')} {away.get('teamName','')}".strip(),
                "tricode": away.get("teamTricode"),
                "score": away.get("score"),
                "wins": away.get("wins"),
                "losses": away.get("losses"),
            },
        }
        # Only pull box scores for finished games (statusCode 3 = Final).
        if g.get("gameStatus") == 3:
            try:
                box = fetch_boxscore(game_id)
                entry["home"]["players"] = _extract_player_stats(box.get("homeTeam", {}))
                entry["away"]["players"] = _extract_player_stats(box.get("awayTeam", {}))
                entry["home"]["teamStats"] = (box.get("homeTeam", {}) or {}).get("statistics")
                entry["away"]["teamStats"] = (box.get("awayTeam", {}) or {}).get("statistics")
            except Exception as e:
                logger.warning("取得 boxscore 失敗 (gameId=%s): %s", game_id, e)
                entry["boxscoreError"] = str(e)
        payload.append(entry)
    return payload


def summarize_with_claude(games_payload: list[dict], report_date: str, aws_region: str) -> str:
    client = anthropic.AnthropicBedrock(aws_region=aws_region)
    user_prompt = (
        f"以下是 {report_date} (美東時間) 的 NBA 比賽資料，請依系統指示撰寫繁體中文每日戰報。\n\n"
        f"```json\n{json.dumps(games_payload, ensure_ascii=False, indent=2)}\n```"
    )
    with client.messages.stream(
        model=BEDROCK_MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        final = stream.get_final_message()
    return "".join(b.text for b in final.content if b.type == "text")


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
    print(f"      MFA 臨時憑證取得成功，有效至 {creds['Expiration']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NBA 每日戰報產生器 (Bedrock)")
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS profile name (對應 ~/.aws/credentials 與 ~/.aws/config)",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="覆蓋 profile 中的 AWS region (例如 us-east-1)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.profile:
        print(f"[*] 使用 AWS profile: {args.profile}")

    import botocore.session
    session = botocore.session.Session(profile=args.profile)
    profile_cfg = session.get_scoped_config()
    aws_region = args.region or profile_cfg.get("region", DEFAULT_REGION)
    mfa_serial = profile_cfg.get("mfa_serial") or os.getenv("AWS_MFA_SERIAL")

    if mfa_serial:
        token_code = input("請輸入 MFA 驗證碼 (6 碼): ").strip()
        if not token_code:
            logger.error("MFA 驗證碼不可為空")
            return 1
        print("[0/3] 取得 MFA 臨時憑證 ...")
        get_mfa_credentials(mfa_serial, token_code, aws_region, args.profile)

    et_now = datetime.now(ZoneInfo("US/Eastern"))
    report_date = et_now.strftime("%Y-%m-%d")

    print(f"[1/3] 擷取 {report_date} (ET) 的 NBA 比賽資料 ...")
    games = fetch_todays_games()
    print(f"      找到 {len(games)} 場比賽")

    payload = build_games_payload(games)

    print(f"[2/3] 呼叫 Claude ({BEDROCK_MODEL}) 彙整報告 ...")
    if not payload:
        report = f"# NBA 每日戰報 — {report_date}\n\n本日美東時間暫無 NBA 賽事。\n"
    else:
        summary = summarize_with_claude(payload, report_date, aws_region)
        report = f"# NBA 每日戰報 — {report_date}\n\n{summary}\n"

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"nba_daily_report_{report_date}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"[3/3] 已寫入：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
