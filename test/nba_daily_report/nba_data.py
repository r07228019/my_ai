"""NBA Live API data fetching utilities."""
from __future__ import annotations

import logging

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
