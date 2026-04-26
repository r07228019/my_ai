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

import anthropic
import yaml

from utils.aws_auth import setup_aws_session
from .nba_data import build_games_payload, build_scores_summary, fetch_todays_games
from .html_gen import generate_website

logging.basicConfig(format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent
REPO_ROOT = PROJECT_DIR.parent.parent
TIMEOUT_SECONDS = 600


def _timeout_handler(_signum, _frame):
    raise TimeoutError(f"整支程式執行超過 {TIMEOUT_SECONDS // 60} 分鐘，已中止")


def load_config() -> dict:
    with open(PROJECT_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_system_prompt(config: dict) -> str:
    return (PROJECT_DIR / config["claude"]["system_prompt_file"]).read_text(encoding="utf-8").strip()


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
    parser.add_argument("--profile", default=None, help="AWS profile name (對應 ~/.aws/credentials 與 ~/.aws/config)")
    parser.add_argument("--region", default=None, help="覆蓋 profile 中的 AWS region (例如 us-east-1)")
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

        scores = build_scores_summary(payload)
        if scores:
            scores_path = output_dir / f"scores_{report_date}.json"
            scores_path.write_text(
                json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"      已寫入比分摘要：{scores_path}")

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
