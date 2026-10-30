"""翌日分の投稿の下書きを Claude で作る。

出力先:
  <out>/auto/<日付>/<account>-<n>.yaml    承認なしで予約してよい投稿（auto_approve: true のアカウントのみ）
  <out>/review/<日付>/<account>-<n>.yaml  承認が必要な投稿（PR にする）
  <out>/pr_body.md                        PR 本文（スマホで読みやすい一覧）

使い方: python sns/generate.py --out build [--date 2026-11-01] [--dry-run]
"""
import argparse
import json
import pathlib
import re
import sys
from datetime import date, datetime, timedelta

import anthropic

from common import (JST, cost_usd, dump_yaml, load_accounts, load_config,
                    load_ledger, now_jst, queue_items, recent_texts,
                    save_ledger, sekki_for, worst_case_usd)

THREADS_MAX_CHARS = 500
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "needs_review": {"type": "boolean"},
                    "review_reason": {"type": "string"},
                },
                "required": ["text", "needs_review", "review_reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["posts"],
    "additionalProperties": False,
}


def system_prompt(acc):
    themes = "\n".join(f"- {t}" for t in acc.get("themes") or [])
    rules = "\n".join(f"- {r}" for r in acc.get("rules") or [])
    return f"""あなたは Threads アカウント「{acc['display_name']}」の中の人として、投稿文を書きます。

# 人物像と口調
{acc['persona']}
# よく扱うテーマ
{themes}
# 守ること
{rules}
- 1投稿は{THREADS_MAX_CHARS}文字以内。ハッシュタグは付けても1つまで。
- ほかのアカウントへの言及・宣伝・相互フォローの呼びかけはしない。
- 過去の投稿と同じ書き出しや同じ内容をくり返さない。

# 承認が必要な投稿
次のどれかに当てはまる投稿は needs_review を true にし、理由を review_reason に短く書く。
当てはまらなければ false にして review_reason は空文字にする。
- リンク・宣伝・告知・キャンペーンを含む
- 時事ニュース、災害、政治、宗教、健康や医療に触れる
- 特定の人物や企業に触れる
- いつもの投稿と雰囲気が大きく違う、または自信がない"""


def user_prompt(acc, day, count):
    recent = recent_texts(acc["id"])
    recent_block = "\n".join(f"- {t}" for t in recent) if recent else "（まだありません）"
    return f"""{day.strftime('%Y年%m月%d日')}（{'月火水木金土日'[day.weekday()]}曜日）に投稿する文章を{count}本書いてください。
投稿時刻は {', '.join(acc['post_times'])}（日本時間）で、この順番に使います。時間帯に合った内容にしてください。
この日の二十四節気はおよそ「{sekki_for(day)}」です。

# 最近の投稿（くり返さないこと）
{recent_block}"""


def call_claude(client, cfg, acc, day, count):
    response = client.beta.messages.create(
        model=cfg["model"],
        max_tokens=cfg["max_tokens"],
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": cfg["effort"], "format": {"type": "json_schema", "schema": SCHEMA}},
        system=system_prompt(acc),
        messages=[{"role": "user", "content": user_prompt(acc, day, count)}],
    )
    # 途中で別モデルに切り替わった場合は、両方のモデル分を高めの単価で数える
    fell_back = any(getattr(it, "type", "") == "fallback_message"
                    for it in getattr(response.usage, "iterations", None) or [])
    usd = cost_usd("unknown" if fell_back else response.model,
                   response.usage.input_tokens, response.usage.output_tokens) * (2 if fell_back else 1)
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{acc['id']}: 生成が断られました")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"{acc['id']}: 出力が途中で切れました")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["posts"], usd


def dry_run_posts(acc, count):
    return [{"text": f"【テスト】{acc['display_name']}の下書き{i + 1}", "needs_review": i == 0,
             "review_reason": "テスト" if i == 0 else ""} for i in range(count)], 0.0


def already_queued(account_id, day):
    prefix = day.isoformat()
    return any(item.get("account") == account_id and item["scheduled_at"].startswith(prefix)
               for _, item in queue_items())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--date", help="対象日（既定は明日）")
    parser.add_argument("--dry-run", action="store_true", help="API を呼ばずにテスト用の下書きを作る")
    args = parser.parse_args()

    day = date.fromisoformat(args.date) if args.date else (now_jst() + timedelta(days=1)).date()
    out = pathlib.Path(args.out)
    cfg = load_config()
    if cfg["monthly_budget_usd"] <= 0 and not args.dry_run:
        print("Claude API の予算が 0 のため、自動生成はしません（課金なし）。")
        return
    ledger = load_ledger()
    client = None if args.dry_run else anthropic.Anthropic()
    reserve = worst_case_usd(cfg["max_tokens"])
    summary, errors = [], []

    for acc in load_accounts().values():
        if not acc.get("enabled") or acc["platform"] != "threads":
            continue
        if already_queued(acc["id"], day):
            print(f"{acc['id']}: {day} の分は作成済みなのでスキップ")
            continue
        if ledger["usd"] + reserve > cfg["monthly_budget_usd"]:
            errors.append(f"今月の予算（${cfg['monthly_budget_usd']}）に届くため {acc['id']} の生成を止めました"
                          f"（使用済み ${ledger['usd']:.2f}）")
            continue
        count = len(acc["post_times"])
        try:
            posts, usd = dry_run_posts(acc, count) if args.dry_run else call_claude(client, cfg, acc, day, count)
        except (anthropic.APIError, RuntimeError, json.JSONDecodeError, StopIteration) as e:
            errors.append(f"{acc['id']}: {e}")
            continue
        if not args.dry_run:
            ledger["usd"] = round(ledger["usd"] + usd, 4)
            ledger["calls"] += 1
            save_ledger(ledger)

        for i, (post, hhmm) in enumerate(zip(posts, acc["post_times"])):
            reasons = [post["review_reason"]] if post["needs_review"] else []
            if URL_RE.search(post["text"]):
                reasons.append("リンクを含む")
            if len(post["text"]) > THREADS_MAX_CHARS:
                reasons.append(f"{THREADS_MAX_CHARS}文字を超えている")
            if not acc.get("auto_approve"):
                reasons.append("このアカウントは全件承認")
            hour, minute = map(int, hhmm.split(":"))
            scheduled = datetime(day.year, day.month, day.day, hour, minute, tzinfo=JST)
            item = {"account": acc["id"], "platform": acc["platform"],
                    "scheduled_at": scheduled.isoformat(), "status": "scheduled",
                    "text": post["text"]}
            bucket = "review" if reasons else "auto"
            if reasons:
                item["review_reason"] = " / ".join(r for r in reasons if r)
            dump_yaml(out / bucket / day.isoformat() / f"{acc['id']}-{i + 1}.yaml", item)
            summary.append((bucket, acc, hhmm, item))

    body = [f"## {day.isoformat()}（{sekki_for(day)}）の投稿案", "",
            "- このPRを**マージ = 承認**です。マージした投稿だけが予定時刻に投稿されます。",
            "- 直したい投稿は「Files changed」から文章を編集、やめたい投稿はファイルを削除してからマージしてください。", ""]
    for bucket, acc, hhmm, item in summary:
        if bucket == "review":
            body += [f"### {acc['display_name']}　{hhmm}", f"> 承認が必要な理由: {item['review_reason']}", "",
                     item["text"], ""]
    auto = [s for s in summary if s[0] == "auto"]
    if auto:
        body += [f"（このほか承認なしで予約した投稿: {len(auto)}件）", ""]
    if errors:
        body += ["### ⚠️ 作れなかったもの", *[f"- {e}" for e in errors], ""]
    body.append(f"今月の Claude API 使用額: ${ledger['usd']:.2f} / ${cfg['monthly_budget_usd']}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "pr_body.md").write_text("\n".join(body) + "\n", encoding="utf-8")
    print("\n".join(body))
    if errors:
        (out / "errors.txt").write_text("\n".join(errors) + "\n", encoding="utf-8")
        for e in errors:
            print(f"::error::{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
