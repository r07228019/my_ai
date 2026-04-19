"""Static website generation: HTML rendering and NBA.com link injection."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_PROJECT_DIR = Path(__file__).parent


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
        sidebar_items.append(
            f'        <li style="animation-delay:{delay}"><a href="{href}"{active}>'
            f'<i class="ph ph-basketball"></i> {r["date"]}</a></li>'
        )
    sidebar = "\n".join(sidebar_items)
    try:
        _d = datetime.strptime(active_date, "%Y-%m-%d")
        hero_date = f"{_d.year} 年 {_d.month} 月 {_d.day} 日"
    except Exception:
        hero_date = active_date

    template = (_PROJECT_DIR / "template.html").read_text(encoding="utf-8")
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

    return len(reports)
