"""Static website generation: HTML rendering and NBA.com link injection."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
_PROJECT_DIR = Path(__file__).parent


def _standings_html(standings: dict) -> str:
    """Generate HTML for the standings page."""
    if not standings or (not standings.get("East") and not standings.get("West")):
        return '<p style="color:var(--text-muted);padding:2rem">球隊排名資料暫時無法取得。</p>'

    season = standings.get("season", "")

    def render_conf(teams: list, conf_name: str) -> str:
        rows_html = ""
        for i, t in enumerate(teams):
            row_class = ""
            if i == 8:
                row_class = ' class="zone-divider playin-zone"'
            elif i == 10:
                row_class = ' class="zone-divider out-zone"'

            clinch = t["clinch"]
            badge = ""
            team_class = ""
            if clinch in ("x", "y", "z"):
                team_class = "clinched"
                badge = ' <span class="clinch-badge">✓</span>'
            elif clinch == "p":
                team_class = "playin"
                badge = ' <span class="clinch-badge playin">PI</span>'
            elif clinch == "e" or clinch == "o":
                team_class = "eliminated"

            streak = t["streak"] or ""
            streak_class = "streak-win" if streak.startswith("W") else "streak-loss"

            rows_html += f"""
      <tr{row_class}>
        <td class="seed-col">{t['seed']}</td>
        <td class="team-col {team_class}">
          <a href="https://www.nba.com/team/{t['team_id']}" target="_blank" rel="noopener" class="team-link">{t['city']} {t['name']}</a>{badge}
        </td>
        <td>{t['wins']}</td><td>{t['losses']}</td><td>{t['pct']}</td>
        <td>{t['gb']}</td>
        <td class="minor-col">{t['home']}</td>
        <td class="minor-col">{t['road']}</td>
        <td class="{streak_class}">{streak}</td>
      </tr>"""

        return f"""
    <div class="conf-standings">
      <h2 class="conf-title"><i class="ph ph-trophy"></i> {conf_name}</h2>
      <table class="standings-table">
        <thead><tr>
          <th>#</th>
          <th data-col="1">球隊</th>
          <th data-col="2" data-numeric="1">勝</th>
          <th data-col="3" data-numeric="1">敗</th>
          <th data-col="4" data-numeric="1">勝率</th>
          <th data-col="5">差距</th>
          <th class="minor-col" data-col="6">主場</th>
          <th class="minor-col" data-col="7">客場</th>
          <th data-col="8">近況</th>
        </tr></thead>
        <tbody>{rows_html}
        </tbody>
      </table>
    </div>"""

    east_html = render_conf(standings["East"], "東區 Eastern Conference")
    west_html = render_conf(standings["West"], "西區 Western Conference")
    return f"""<div class="standings-page">
  <p class="data-note">{season} 賽季常規賽成績　✓ 晉季後賽　PI 附加賽</p>
  {east_html}
  {west_html}
</div>"""


def _playoffs_html(playoff_data: dict) -> str:
    """Generate HTML for the playoffs bracket page."""
    if not playoff_data or (not playoff_data.get("East") and not playoff_data.get("West")):
        return '<p style="color:var(--text-muted);padding:2rem">季後賽資料暫時無法取得。</p>'

    season = playoff_data.get("season", "")
    updated_at = playoff_data.get("updated_at", "")

    def render_conf(matchups: list, conf_name: str) -> str:
        if not matchups:
            return ""
        matchups_html = ""
        for m in matchups:
            hw = m["high_wins"]
            lw = m["low_wins"]
            total = hw + lw
            if hw == 4:
                status, top_cls, bot_cls = f"晉級 {hw}–{lw}", "winner", "loser"
            elif lw == 4:
                status, top_cls, bot_cls = f"淘汰 {hw}–{lw}", "loser", "winner"
            elif total > 0:
                status = f"系列賽 {hw}–{lw}"
                top_cls = "leading" if hw > lw else ("trailing" if hw < lw else "")
                bot_cls = "leading" if lw > hw else ("trailing" if lw < hw else "")
            else:
                status, top_cls, bot_cls = "即將開打", "", ""

            matchups_html += f"""
        <div class="matchup">
          <div class="matchup-team {top_cls}">
            <span class="matchup-seed">{m['high_seed']}</span>
            <a href="https://www.nba.com/team/{m['high_team_id']}" target="_blank" rel="noopener" class="team-link matchup-name">{m['high_team']}</a>
            <span class="matchup-wins">{hw if total > 0 else ''}</span>
          </div>
          <div class="matchup-status">{status}</div>
          <div class="matchup-team {bot_cls}">
            <span class="matchup-seed">{m['low_seed']}</span>
            <a href="https://www.nba.com/team/{m['low_team_id']}" target="_blank" rel="noopener" class="team-link matchup-name">{m['low_team']}</a>
            <span class="matchup-wins">{lw if total > 0 else ''}</span>
          </div>
        </div>"""

        return f"""
      <div class="bracket-conf">
        <h3 class="bracket-conf-title">{conf_name}</h3>
        <div class="bracket-round-label"><i class="ph ph-flag-pennant"></i> 首輪 First Round</div>
        <div class="bracket-matchups">{matchups_html}
        </div>
      </div>"""

    west_html = render_conf(playoff_data.get("West", []), "西區 Western Conference")
    east_html = render_conf(playoff_data.get("East", []), "東區 Eastern Conference")
    updated_line = f'<p class="data-note updated-at">最後更新：{updated_at}</p>' if updated_at else ""
    return f"""<div class="playoffs-page">
  <p class="data-note">{season} 賽季季後賽　· 資料來源：NBA API</p>
  {updated_line}
  <div class="bracket-grid">
    {west_html}
    {east_html}
  </div>
</div>"""


def _render_page(
    title: str,
    body_html: str,
    reports: list[dict],
    active_date: str,
    base_path: str,
    hero_label: str = "NBA Daily Report",
    hero_date_override: str | None = None,
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
        sidebar_items.append(
            f'        <li style="animation-delay:{delay}"><a href="{href}"{active}>'
            f'<i class="ph ph-basketball"></i> {r["date"]}</a></li>'
        )
    sidebar = "\n".join(sidebar_items)

    standings_active = ' aria-current="page"' if active_date == "standings" else ""
    playoffs_active = ' aria-current="page"' if active_date == "playoffs" else ""
    nav_links = (
        f'    <li><a href="{base_path}standings.html"{standings_active}><i class="ph ph-trophy"></i> 球隊排名</a></li>\n'
        f'    <li><a href="{base_path}playoffs.html"{playoffs_active}><i class="ph ph-brackets-curly"></i> 季後賽形勢</a></li>'
    )

    if hero_date_override:
        hero_date = hero_date_override
    else:
        try:
            _d = datetime.strptime(active_date, "%Y-%m-%d")
            hero_date = f"{_d.year} 年 {_d.month} 月 {_d.day} 日"
        except Exception:
            hero_date = active_date

    template = (_PROJECT_DIR / "template.html").read_text(encoding="utf-8")
    return (
        template
        .replace("%%TITLE%%", title)
        .replace("%%NAV_LINKS%%", nav_links)
        .replace("%%SIDEBAR%%", sidebar)
        .replace("%%HERO_LABEL%%", hero_label)
        .replace("%%HERO_DATE%%", hero_date)
        .replace("%%BODY_HTML%%", body_html)
    )


def _build_player_url_map() -> dict[str, tuple[int, str]]:
    """Return {full_name: (player_id, slug)} for all active NBA players (no network call)."""
    import unicodedata
    from nba_api.stats.static import players as nba_players

    def to_slug(name: str) -> str:
        name = unicodedata.normalize("NFKD", name)
        name = "".join(c for c in name if not unicodedata.combining(c))
        name = name.lower().replace("'", "").replace("`", "")
        name = re.sub(r"\s+", "-", name.strip())
        name = re.sub(r"[^a-z0-9-]", "", name)
        return name

    return {
        p["full_name"]: (p["id"], to_slug(p["full_name"]))
        for p in nba_players.get_players()
        if p.get("is_active")
    }


def _linkify_players(html: str, player_map: dict) -> str:
    """Replace full English player names in HTML text nodes with NBA.com hyperlinks."""
    sorted_names = sorted(player_map.keys(), key=len, reverse=True)
    relevant = [n for n in sorted_names if n in html]
    if not relevant:
        return html

    pattern = re.compile("(" + "|".join(re.escape(n) for n in relevant) + ")")

    def replacer(m: re.Match) -> str:
        name = m.group(0)
        pid, slug = player_map[name]
        return f'<a href="https://www.nba.com/player/{pid}/{slug}" target="_blank" rel="noopener" class="player-link">{name}</a>'

    parts = re.split(r"(<[^>]+>)", html)
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
    tricodes = {k: v for k, v in team_map.items() if k.isupper() and len(k) <= 3}
    full_names = {k: v for k, v in team_map.items() if not (k.isupper() and len(k) <= 3)}

    relevant_tricodes = [k for k in tricodes if k in html]
    relevant_names = sorted([k for k in full_names if k in html], key=len, reverse=True)

    if not relevant_tricodes and not relevant_names:
        return html

    patterns = [re.escape(n) for n in relevant_names] + [r"\b" + re.escape(t) + r"\b" for t in relevant_tricodes]
    combined = re.compile("(" + "|".join(patterns) + ")")
    lookup = {**{n: full_names[n] for n in relevant_names}, **{t: tricodes[t] for t in relevant_tricodes}}

    def replacer(m: re.Match) -> str:
        name = m.group(0)
        tid, slug = lookup[name]
        return f'<a href="https://www.nba.com/team/{tid}/{slug}" target="_blank" rel="noopener" class="team-link">{name}</a>'

    parts = re.split(r"(<[^>]+>)", html)
    return "".join(
        part if part.startswith("<") else combined.sub(replacer, part)
        for part in parts
    )


def _linkify_report_date(html: str, date_str: str) -> str:
    """Wrap the Chinese date in the H1 title with a link to nba.com/games?date=YYYY-MM-DD."""
    url = f"https://www.nba.com/games?date={date_str}"
    date_pattern = re.compile(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)")
    replacement = f'<a href="{url}" target="_blank" rel="noopener" class="date-link">\\1</a>'
    return re.sub(
        r"(<h1[^>]*>)(.*?)(</h1>)",
        lambda m: m.group(1) + date_pattern.sub(replacement, m.group(2), count=1) + m.group(3),
        html,
        count=1,
        flags=re.DOTALL,
    )


def generate_website(report_dir: Path, docs_dir: Path, keep_n: int = 10) -> int:
    """Generate static HTML site from the last keep_n markdown reports.

    Returns the number of reports rendered.
    """
    import markdown as md_lib
    from .nba_data import fetch_standings, fetch_playoff_picture

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

    (docs_dir / "index.html").write_text(
        _render_page(f"NBA Daily Report — {reports[0]['date']}", reports[0]["body"], reports, reports[0]["date"], base_path=""),
        encoding="utf-8",
    )

    for report in reports:
        (docs_reports_dir / report["filename"]).write_text(
            _render_page(f"NBA Daily Report — {report['date']}", report["body"], reports, report["date"], base_path="../"),
            encoding="utf-8",
        )

    for old in sorted(docs_reports_dir.glob("nba_daily_report_*.html"), reverse=True)[keep_n:]:
        old.unlink()

    # Standings page
    try:
        print("      取得球隊排名資料 ...")
        standings = fetch_standings()
        season = standings.get("season", "")
    except Exception as e:
        logger.warning("無法取得球隊排名: %s", e)
        standings, season = {}, ""
    (docs_dir / "standings.html").write_text(
        _render_page(
            f"NBA 球隊排名 {season}",
            _standings_html(standings),
            reports,
            active_date="standings",
            base_path="",
            hero_label="球隊排名 Standings",
            hero_date_override=f"{season} 賽季" if season else "本賽季",
        ),
        encoding="utf-8",
    )

    # Playoffs page
    try:
        print("      取得季後賽形勢資料 ...")
        playoff_data = fetch_playoff_picture()
    except Exception as e:
        logger.warning("無法取得季後賽資料: %s", e)
        playoff_data = {}
    playoff_season = playoff_data.get("season", season)
    (docs_dir / "playoffs.html").write_text(
        _render_page(
            f"NBA 季後賽 {playoff_season}",
            _playoffs_html(playoff_data),
            reports,
            active_date="playoffs",
            base_path="",
            hero_label="季後賽形勢 Playoffs",
            hero_date_override=f"{playoff_season} 季後賽" if playoff_season else "本賽季",
        ),
        encoding="utf-8",
    )

    return len(reports)
