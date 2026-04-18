"""Fetch today's NBA games, summarize with Claude via AWS Bedrock, save as Markdown."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

logging.basicConfig(format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import anthropic
from nba_api.live.nba.endpoints import boxscore, scoreboard

from utils.aws_auth import setup_aws_session

PROJECT_DIR = Path(__file__).parent


def load_config() -> dict:
    config_path = PROJECT_DIR / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_system_prompt(config: dict) -> str:
    prompt_file = PROJECT_DIR / config["claude"]["system_prompt_file"]
    return prompt_file.read_text(encoding="utf-8").strip()


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


def summarize_with_claude(
    games_payload: list[dict], report_date: str, aws_region: str, config: dict, system_prompt: str,
) -> str:
    client = anthropic.AnthropicBedrock(aws_region=aws_region)
    bedrock_model = config["aws"]["bedrock_model"]
    max_tokens = config["claude"]["max_tokens"]
    user_prompt = (
        f"以下是 {report_date} (美東時間) 的 NBA 比賽資料，請依系統指示撰寫繁體中文每日戰報。\n\n"
        f"```json\n{json.dumps(games_payload, ensure_ascii=False, indent=2)}\n```"
    )
    with client.messages.stream(
        model=bedrock_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        final = stream.get_final_message()
    usage = final.usage
    print(f"      Token 用量：input={usage.input_tokens}, output={usage.output_tokens}, total={usage.input_tokens + usage.output_tokens}")
    return "".join(b.text for b in final.content if b.type == "text")


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
    config = load_config()
    system_prompt = load_system_prompt(config)

    default_region = config["aws"]["default_region"]
    bedrock_model = config["aws"]["bedrock_model"]
    output_dir = PROJECT_DIR / config["output"]["dir"]

    profile = args.profile or config["aws"].get("default_profile")
    if profile:
        print(f"[*] 使用 AWS profile: {profile}")

    try:
        aws_region = setup_aws_session(profile, args.region, default_region)
    except ValueError as e:
        logger.error("%s", e)
        return 1

    et_now = datetime.now(ZoneInfo("US/Eastern"))
    report_date = et_now.strftime("%Y-%m-%d")

    print(f"[1/3] 擷取 {report_date} (ET) 的 NBA 比賽資料 ...")
    games = fetch_todays_games()
    print(f"      找到 {len(games)} 場比賽")

    payload = build_games_payload(games)

    print(f"[2/3] 呼叫 Claude ({bedrock_model}) 彙整報告 ...")
    if not payload:
        report = f"# NBA 每日戰報 — {report_date}\n\n本日美東時間暫無 NBA 賽事。\n"
    else:
        summary = summarize_with_claude(payload, report_date, aws_region, config, system_prompt)
        report = f"# NBA 每日戰報 — {report_date}\n\n{summary}\n"

    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / f"nba_daily_report_{report_date}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"[3/3] 已寫入：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
