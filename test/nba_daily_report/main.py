"""Fetch today's NBA games, summarize with Claude via AWS Bedrock, save as Markdown."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
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
REPO_ROOT = PROJECT_DIR.parent.parent
TIMEOUT_SECONDS = 600


def _timeout_handler(_signum, _frame):
    raise TimeoutError(f"整支程式執行超過 {TIMEOUT_SECONDS // 60} 分鐘，已中止")


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


def _render_page(
    title: str,
    body_html: str,
    reports: list[dict],
    active_date: str,
    base_path: str,
) -> str:
    """Render a full HTML page by filling template.html with report data."""
    sidebar_items = []
    for i, r in enumerate(reports):
        href = (
            f"{base_path}index.html"
            if r["date"] == reports[0]["date"]
            else f"{base_path}reports/{r['filename']}"
        )
        active = ' aria-current="page"' if r["date"] == active_date else ""
        delay = f"{i * 0.06 + 0.05:.2f}s"
        sidebar_items.append(f'        <li style="animation-delay:{delay}"><a href="{href}"{active}><i class="ph ph-basketball"></i> {r["date"]}</a></li>')
    sidebar = "\n".join(sidebar_items)
    try:
        from datetime import datetime as _dt
        _d = _dt.strptime(active_date, "%Y-%m-%d")
        hero_date = f"{_d.year} 年 {_d.month} 月 {_d.day} 日"
    except Exception:
        hero_date = active_date

    template = (PROJECT_DIR / "template.html").read_text(encoding="utf-8")
    return (
        template
        .replace("%%TITLE%%", title)
        .replace("%%SIDEBAR%%", sidebar)
        .replace("%%HERO_DATE%%", hero_date)
        .replace("%%BODY_HTML%%", body_html)
    )


def _build_player_url_map() -> dict[str, tuple[int, str]]:
    """Return {full_name: (player_id, slug)} for all active NBA players (no network call)."""
    import unicodedata
    import re as _re
    from nba_api.stats.static import players as nba_players

    def to_slug(name: str) -> str:
        name = unicodedata.normalize("NFKD", name)
        name = "".join(c for c in name if not unicodedata.combining(c))
        name = name.lower().replace("'", "").replace("`", "")
        name = _re.sub(r"\s+", "-", name.strip())
        name = _re.sub(r"[^a-z0-9-]", "", name)
        return name

    return {
        p["full_name"]: (p["id"], to_slug(p["full_name"]))
        for p in nba_players.get_players()
        if p.get("is_active")
    }


def _linkify_players(html: str, player_map: dict) -> str:
    """Replace full English player names in HTML text nodes with NBA.com hyperlinks."""
    import re as _re

    sorted_names = sorted(player_map.keys(), key=len, reverse=True)
    relevant = [n for n in sorted_names if n in html]
    if not relevant:
        return html

    pattern = _re.compile("(" + "|".join(_re.escape(n) for n in relevant) + ")")

    def replacer(m: _re.Match) -> str:
        name = m.group(0)
        pid, slug = player_map[name]
        return f'<a href="https://www.nba.com/player/{pid}/{slug}" target="_blank" rel="noopener" class="player-link">{name}</a>'

    parts = _re.split(r"(<[^>]+>)", html)
    return "".join(
        part if part.startswith("<") else pattern.sub(replacer, part)
        for part in parts
    )


def _build_team_url_map() -> dict[str, tuple[int, str]]:
    """Return {abbreviation: (team_id, slug)} and {full_name: (team_id, slug)} for all NBA teams."""
    from nba_api.stats.static import teams as nba_teams

    result = {}
    for t in nba_teams.get_teams():
        slug = t["nickname"].lower()
        tid = t["id"]
        result[t["abbreviation"]] = (tid, slug)
        result[t["full_name"]] = (tid, slug)
    return result


def _linkify_teams(html: str, team_map: dict) -> str:
    """Replace team abbreviations and full English team names in HTML text nodes with NBA.com links."""
    import re as _re

    tricodes = {k: v for k, v in team_map.items() if k.isupper() and len(k) <= 3}
    full_names = {k: v for k, v in team_map.items() if not (k.isupper() and len(k) <= 3)}

    relevant_tricodes = [k for k in tricodes if k in html]
    relevant_names = sorted([k for k in full_names if k in html], key=len, reverse=True)

    if not relevant_tricodes and not relevant_names:
        return html

    patterns = [_re.escape(n) for n in relevant_names] + [r"\b" + _re.escape(t) + r"\b" for t in relevant_tricodes]
    combined = _re.compile("(" + "|".join(patterns) + ")")
    lookup = {**{n: full_names[n] for n in relevant_names}, **{t: tricodes[t] for t in relevant_tricodes}}

    def replacer(m: _re.Match) -> str:
        name = m.group(0)
        tid, slug = lookup[name]
        return f'<a href="https://www.nba.com/team/{tid}/{slug}" target="_blank" rel="noopener" class="team-link">{name}</a>'

    parts = _re.split(r"(<[^>]+>)", html)
    return "".join(
        part if part.startswith("<") else combined.sub(replacer, part)
        for part in parts
    )


def _linkify_report_date(html: str, date_str: str) -> str:
    """Wrap the Chinese date in the H1 title with a link to nba.com/games?date=YYYY-MM-DD."""
    import re as _re

    url = f"https://www.nba.com/games?date={date_str}"
    date_pattern = _re.compile(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)")
    replacement = f'<a href="{url}" target="_blank" rel="noopener" class="date-link">\\1</a>'
    # Only replace inside the first <h1> block
    return _re.sub(
        r"(<h1[^>]*>)(.*?)(</h1>)",
        lambda m: m.group(1) + date_pattern.sub(replacement, m.group(2), count=1) + m.group(3),
        html,
        count=1,
        flags=_re.DOTALL,
    )


def generate_website(report_dir: Path, docs_dir: Path, keep_n: int = 10) -> int:
    """Generate static HTML site from the last keep_n markdown reports.

    Returns the number of reports rendered.
    """
    import markdown as md_lib

    md_files = sorted(report_dir.glob("nba_daily_report_*.md"), reverse=True)[:keep_n]
    if not md_files:
        return 0

    docs_reports_dir = docs_dir / "reports"
    docs_reports_dir.mkdir(parents=True, exist_ok=True)

    player_map = _build_player_url_map()
    team_map = _build_team_url_map()

    reports = [
        {
            "date": p.stem.replace("nba_daily_report_", ""),
            "body": _linkify_report_date(
                _linkify_teams(
                    _linkify_players(
                        md_lib.markdown(p.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"]),
                        player_map,
                    ),
                    team_map,
                ),
                p.stem.replace("nba_daily_report_", ""),
            ),
            "filename": f"{p.stem}.html",
        }
        for p in md_files
    ]

    # index.html = latest report
    (docs_dir / "index.html").write_text(
        _render_page(f"NBA Daily Report — {reports[0]['date']}", reports[0]["body"], reports, reports[0]["date"], base_path=""),
        encoding="utf-8",
    )

    # individual pages
    for report in reports:
        (docs_reports_dir / report["filename"]).write_text(
            _render_page(f"NBA Daily Report — {report['date']}", report["body"], reports, report["date"], base_path="../"),
            encoding="utf-8",
        )

    # remove html files beyond keep_n
    for old in sorted(docs_reports_dir.glob("nba_daily_report_*.html"), reverse=True)[keep_n:]:
        old.unlink()

    return len(reports)


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
    start = time.perf_counter()
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(TIMEOUT_SECONDS)

    try:
        args = parse_args()
        config = load_config()
        system_prompt = load_system_prompt(config)

        default_region = config["aws"]["default_region"]
        bedrock_model = config["aws"]["bedrock_model"]
        output_dir = PROJECT_DIR / config["output"]["dir"]
        docs_dir = REPO_ROOT / config["output"]["docs_dir"]

        profile = args.profile or config["aws"].get("default_profile")
        if profile:
            print(f"[*] 使用 AWS profile: {profile}")

        aws_region = setup_aws_session(profile, args.region, default_region)

        et_now = datetime.now(ZoneInfo("US/Eastern"))
        report_date = et_now.strftime("%Y-%m-%d")

        print(f"[1/4] 擷取 {report_date} (ET) 的 NBA 比賽資料 ...")
        games = fetch_todays_games()
        print(f"      找到 {len(games)} 場比賽")

        payload = build_games_payload(games)

        print(f"[2/4] 呼叫 Claude ({bedrock_model}) 彙整報告 ...")
        if not payload:
            report = f"# 🏀 NBA 每日戰報 — {report_date}\n\n本日美東時間暫無 NBA 賽事。\n"
        else:
            summary = summarize_with_claude(payload, report_date, aws_region, config, system_prompt)
            report = f"{summary}\n"

        output_dir.mkdir(exist_ok=True)
        out_path = output_dir / f"nba_daily_report_{report_date}.md"
        out_path.write_text(report, encoding="utf-8")
        print(f"[3/4] 已寫入：{out_path}")

        print(f"[4/4] 更新靜態網頁 ...")
        n = generate_website(output_dir, docs_dir)
        print(f"      已產生 {n} 份報告至 {docs_dir}")
        return 0
    except (ValueError, TimeoutError) as e:
        logger.error("%s", e)
        return 1
    finally:
        signal.alarm(0)
        elapsed = time.perf_counter() - start
        print(f"[*] 總執行時間：{elapsed:.2f} 秒")


if __name__ == "__main__":
    sys.exit(main())
