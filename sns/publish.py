"""予約キューのうち、予定時刻を過ぎた投稿を Threads に投稿する。

main ブランチにある投稿 = 承認済み。投稿後はファイルの status を posted に書き換える。
使い方: python sns/publish.py [--dry-run]
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import requests

from common import dump_yaml, load_accounts, now_jst, queue_items

API = "https://graph.threads.net/v1.0"
# 承認が遅れて予定時刻を大きく過ぎた投稿は、季節や時間帯がずれるので投稿しない
TOO_LATE = timedelta(hours=6)
MAX_ATTEMPTS = 3


def post_to_threads(token, text):
    res = requests.post(f"{API}/me/threads", timeout=30,
                        data={"media_type": "TEXT", "text": text, "access_token": token})
    res.raise_for_status()
    creation_id = res.json()["id"]
    res = requests.post(f"{API}/me/threads_publish", timeout=30,
                        data={"creation_id": creation_id, "access_token": token})
    res.raise_for_status()
    return res.json()["id"]


def error_message(e):
    # トークンがログに出ないよう、URL を含む例外文字列ではなく API の返答だけを残す
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}: {e.response.text[:300]}"
    return type(e).__name__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    accounts = load_accounts()
    now = now_jst()
    failed = False

    for path, item in queue_items():
        if item.get("status") != "scheduled":
            continue
        due = datetime.fromisoformat(item["scheduled_at"])
        if due > now:
            continue
        acc = accounts.get(item["account"])
        if not acc or not acc.get("enabled") or acc["platform"] != "threads":
            continue
        if now - due > TOO_LATE:
            item["status"] = "expired"
            dump_yaml(path, item)
            print(f"{path.name}: 予定時刻を{TOO_LATE}以上過ぎたので投稿しません")
            continue

        token = os.environ.get(acc["token_secret"])
        if not token and not args.dry_run:
            print(f"::error::{acc['token_secret']} が設定されていません（{path.name}）")
            failed = True
            continue
        try:
            post_id = "dry-run" if args.dry_run else post_to_threads(token, item["text"])
        except (requests.RequestException, KeyError) as e:
            item["attempts"] = item.get("attempts", 0) + 1
            item["last_error"] = error_message(e)
            if item["attempts"] >= MAX_ATTEMPTS:
                item["status"] = "error"
            dump_yaml(path, item)
            print(f"::error::{path.name}: 投稿に失敗 {item['last_error']}")
            failed = True
            continue
        item["status"] = "posted"
        item["posted_at"] = now.isoformat(timespec="seconds")
        item["post_id"] = post_id
        item.pop("last_error", None)
        dump_yaml(path, item)
        print(f"{path.name}: 投稿しました")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
