"""NBA Live API data fetching utilities."""
from __future__ import annotations

import logging
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo
from nba_api.live.nba.endpoints import boxscore
from nba_api.stats.endpoints import scoreboardv2, scoreboardv3

logger = logging.getLogger(__name__)


def fetch_games_by_et_date(et_date: str) -> list[dict]:
    """Fetch games on a specific ET date (YYYY-MM-DD).

    Primary: ScoreboardV3 — preferred, returns the same shape as the live
    endpoint so downstream code can reuse it without changes.
    Fallback: ScoreboardV2 — used when V3 returns an empty list even though
    games did occur (a known upstream data issue; see nba_api#596).
    V2's result sets are normalized into the V3 `games` shape before return.
    """
    sb = scoreboardv3.ScoreboardV3(game_date=et_date, league_id="00")
    games = sb.get_dict().get("scoreboard", {}).get("games", [])
    if games:
        return games

    v2_games = _fetch_games_v2(et_date)
    if v2_games:
        logger.warning(
            "ScoreboardV3 回傳 0 場 (ET=%s)，已 fallback 至 ScoreboardV2，取得 %d 場",
            et_date, len(v2_games),
        )
    return v2_games


def _fetch_games_v2(et_date: str) -> list[dict]:
    """Fetch games via ScoreboardV2 and normalize to V3 `games` shape.

    V2 expects MM/DD/YYYY; result comes back as multiple resultSets which we
    stitch together (GameHeader + LineScore + SeriesStandings) into the V3
    per-game dict used downstream.
    """
    dt = datetime.strptime(et_date, "%Y-%m-%d")
    v2_date = dt.strftime("%m/%d/%Y")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        raw = scoreboardv2.ScoreboardV2(game_date=v2_date, league_id="00").get_dict()
    result_sets = {rs["name"]: rs for rs in raw.get("resultSets", [])}

    def rows_as_dicts(name: str) -> list[dict]:
        rs = result_sets.get(name) or {}
        return [dict(zip(rs.get("headers", []), row)) for row in rs.get("rowSet", [])]

    headers = rows_as_dicts("GameHeader")
    if not headers:
        return []
    line_by_game: dict[str, list[dict]] = {}
    for ls in rows_as_dicts("LineScore"):
        line_by_game.setdefault(ls["GAME_ID"], []).append(ls)
    series_by_game = {s["GAME_ID"]: s for s in rows_as_dicts("SeriesStandings")}

    games = []
    seen_gids: set[str] = set()
    for h in headers:
        gid = h["GAME_ID"]
        if gid in seen_gids:
            continue
        seen_gids.add(gid)
        home_id, away_id = h["HOME_TEAM_ID"], h["VISITOR_TEAM_ID"]
        lines = {ls["TEAM_ID"]: ls for ls in line_by_game.get(gid, [])}
        home_ls, away_ls = lines.get(home_id, {}), lines.get(away_id, {})

        def team_block(ls: dict) -> dict:
            wins, losses = _parse_wins_losses(ls.get("TEAM_WINS_LOSSES"))
            return {
                "teamId": ls.get("TEAM_ID"),
                "teamCity": ls.get("TEAM_CITY_NAME") or "",
                "teamName": ls.get("TEAM_NAME") or "",
                "teamTricode": ls.get("TEAM_ABBREVIATION") or "",
                "score": ls.get("PTS"),
                "wins": wins,
                "losses": losses,
            }

        entry = {
            "gameId": gid,
            "gameStatus": h.get("GAME_STATUS_ID"),
            "gameStatusText": h.get("GAME_STATUS_TEXT", ""),
            "arena": {"arenaName": h.get("ARENA_NAME") or ""},
            "homeTeam": team_block(home_ls),
            "awayTeam": team_block(away_ls),
        }

        series = series_by_game.get(gid)
        if series is not None:
            entry.update(_series_fields_from_v2(series, entry["homeTeam"], entry["awayTeam"]))
        games.append(entry)
    return games


def _parse_wins_losses(text: str | None) -> tuple[int | None, int | None]:
    if not text or "-" not in text:
        return None, None
    try:
        w, l = text.split("-", 1)
        return int(w), int(l)
    except ValueError:
        return None, None


def _series_fields_from_v2(series: dict, home: dict, away: dict) -> dict:
    """Derive V3-style series fields from V2 SeriesStandings row.

    V2 only exposes HOME_TEAM_WINS/LOSSES and SERIES_LEADER (team city or
    'Tied'), so gameLabel/poRoundDesc/seriesGameNumber can't be recovered —
    leave them blank; downstream only treats `seriesText` as authoritative.
    """
    hw = series.get("HOME_TEAM_WINS") or 0
    hl = series.get("HOME_TEAM_LOSSES") or 0
    if not hw and not hl:
        return {}
    leader = (series.get("SERIES_LEADER") or "").strip()
    if leader.lower() == "tied":
        series_text = f"Tied {hw}-{hl}"
    else:
        leader_tri = home["teamTricode"] if leader == home.get("teamCity") else away["teamTricode"]
        max_w, min_w = max(hw, hl), min(hw, hl)
        verb = "wins" if max_w >= 4 else "leads"
        series_text = f"{leader_tri} {verb} {max_w}-{min_w}"
    return {
        "gameLabel": "",
        "poRoundDesc": "",
        "seriesGameNumber": "",
        "seriesText": series_text,
        "ifNecessary": False,
    }


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
        series_text = g.get("seriesText") or ""
        game_label = g.get("gameLabel") or ""
        if series_text or game_label:
            entry["series"] = {
                "label": game_label,
                "round": g.get("poRoundDesc") or "",
                "gameNumber": g.get("seriesGameNumber") or "",
                "seriesText": series_text,
                "ifNecessary": bool(g.get("ifNecessary")),
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


def _top_scorer(players: list[dict]) -> dict | None:
    """Return the player with highest points from a players list, or None."""
    if not players:
        return None
    def pts(p):
        try:
            return int(p.get("points") or 0)
        except (TypeError, ValueError):
            return 0
    return max(players, key=pts)


def build_scores_summary(payload: list[dict]) -> list[dict]:
    """Build a compact per-game summary (finals only) for the HTML scoreboard block.

    Each entry: {gameId, home/away: {team, tricode, teamId, score, wins, losses, leader}}.
    leader: {name, points, rebounds, assists, position} — top scorer on that team.
    """
    from nba_api.stats.static import teams as nba_teams
    tri_to_id = {t["abbreviation"]: t["id"] for t in nba_teams.get_teams()}

    summary = []
    for entry in payload:
        if entry.get("statusCode") != 3:
            continue
        out = {"gameId": entry.get("gameId"), "status": entry.get("status", "Final")}
        for side in ("home", "away"):
            s = entry.get(side, {})
            leader_raw = _top_scorer(s.get("players") or [])
            leader = None
            if leader_raw:
                leader = {
                    "name": leader_raw.get("name"),
                    "position": leader_raw.get("position") or "",
                    "points": leader_raw.get("points"),
                    "rebounds": leader_raw.get("rebounds"),
                    "assists": leader_raw.get("assists"),
                }
            out[side] = {
                "team": s.get("team"),
                "tricode": s.get("tricode"),
                "teamId": tri_to_id.get(s.get("tricode")),
                "score": s.get("score"),
                "wins": s.get("wins"),
                "losses": s.get("losses"),
                "leader": leader,
            }
        summary.append(out)
    return summary


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
            "home": t.get("HOME", ""),
            "road": t.get("ROAD", ""),
            "clinch": t.get("ClinchIndicator", "") or "",
        }
        if t.get("Conference") == "East":
            east.append(entry)
        else:
            west.append(entry)
    east.sort(key=lambda x: x["seed"])
    west.sort(key=lambda x: x["seed"])
    return {"East": east, "West": west, "season": season}


