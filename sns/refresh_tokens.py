"""Threads の長期トークン（有効期限60日）を延長し、新しいトークンを GitHub Secrets に保存し直す。

GitHub Actions から2週間ごとに実行する。gh コマンドと、Secrets を書き換えられる GH_TOKEN が必要。
"""
import os
import subprocess
import sys

import requests

from common import load_accounts

URL = "https://graph.threads.net/refresh_access_token"


def main():
    failed = False
    for acc in load_accounts().values():
        if acc["platform"] != "threads" or not acc.get("enabled"):
            continue
        name = acc["token_secret"]
        token = os.environ.get(name)
        if not token:
            print(f"::warning::{name} が未設定なのでスキップ")
            continue
        res = requests.get(URL, timeout=30,
                           params={"grant_type": "th_refresh_token", "access_token": token})
        if not res.ok:
            print(f"::error::{name} の延長に失敗 HTTP {res.status_code}: {res.text[:300]}")
            failed = True
            continue
        data = res.json()
        new_token = data["access_token"]
        print(f"::add-mask::{new_token}")
        # 標準入力で渡し、トークンがコマンドラインやログに出ないようにする
        subprocess.run(["gh", "secret", "set", name], input=new_token, text=True, check=True)
        print(f"{name}: 延長しました（あと約{data.get('expires_in', 0) // 86400}日有効）")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
