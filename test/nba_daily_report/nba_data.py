"""NBA Live API data fetching utilities."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from nba_api.live.nba.endpoints import boxscore, scoreboard

logger = logging.getLogger(__name__)


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
        entry = {
            "gameId": game_id,
            "status": g.get("gameStatusText", ""),
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


def _current_season() -> str:
    """Return current NBA season string like '2025-26'."""
    now = datetime.now(ZoneInfo("US/Eastern"))
    y = now.year
    if now.month >= 10:
        return f"{y}-{str(y + 1)[2:]}"
    return f"{y - 1}-{str(y)[2:]}"


def fetch_standings() -> dict:
    """Fetch current conference standings. Returns {'East': [...], 'West': [...], 'season': str}."""
    from nba_api.stats.endpoints import leaguestandingsv3
    season = _current_season()
    raw = leaguestandingsv3.LeagueStandingsV3(
        league_id="00", season=season, season_type="Regular Season"
    )
    rs = raw.get_dict()["resultSets"][0]
    headers, rows = rs["headers"], rs["rowSet"]
    east, west = [], []
    for row in rows:
        t = dict(zip(headers, row))
        gb = t.get("ConferenceGamesBack")
        entry = {
            "seed": t.get("PlayoffRank") or 0,
            "team_id": t.get("TeamID"),
            "city": t.get("TeamCity", ""),
            "name": t.get("TeamName", ""),
            "wins": t.get("WINS", 0),
            "losses": t.get("LOSSES", 0),
            "pct": f"{float(t.get('WinPCT') or 0):.3f}",
            "gb": "-" if not gb or gb == 0 else str(gb),
            "streak": t.get("strCurrentStreak", ""),
            "home": t.get("Home", ""),
            "road": t.get("Road", ""),
            "clinch": t.get("ClinchIndicator", "") or "",
        }
        if t.get("Conference") == "East":
            east.append(entry)
        else:
            west.append(entry)
    east.sort(key=lambda x: x["seed"])
    west.sort(key=lambda x: x["seed"])
    return {"East": east, "West": west, "season": season}


def fetch_playoff_picture() -> dict:
    """Fetch current playoff bracket data. Returns {'East': [...], 'West': [...], 'season': str, 'updated_at': str}."""
    from nba_api.stats.endpoints import playoffpicture
    season = _current_season()
    season_year = int(season.split("-")[0])
    raw = playoffpicture.PlayoffPicture(league_id="00", season_id=f"2{season_year}")
    result_sets = raw.get_dict()["resultSets"]

    def parse_conf(rs: dict) -> list[dict]:
        headers, rows = rs["headers"], rs["rowSet"]
        return sorted(
            [
                {
                    "seed": t.get("CURRENT_SEED", 0),
                    "team_id": t.get("TEAM_ID"),
                    "city": t.get("TEAM_CITY", ""),
                    "name": t.get("TEAM_NICKNAME", ""),
                    "series_wins": t.get("SERIES_WINS") or 0,
                    "series_losses": t.get("SERIES_LOSSES") or 0,
                    "vs_seed": t.get("VS_SEED"),
                    "vs_city": t.get("VS_CITY", ""),
                    "vs_name": t.get("VS_NAME", ""),
                    "vs_id": t.get("VS_TEAM_ID"),
                }
                for t in [dict(zip(headers, row)) for row in rows]
            ],
            key=lambda x: x["seed"],
        )

    tpe_now = datetime.now(ZoneInfo("Asia/Taipei"))
    return {
        "East": parse_conf(result_sets[0]),   # EastConfPlayoffPicture
        "West": parse_conf(result_sets[1]),   # WestConfPlayoffPicture
        "season": season,
        "updated_at": tpe_now.strftime("%Y-%m-%d %H:%M 台北時間"),
    }
