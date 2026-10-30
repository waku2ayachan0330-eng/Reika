"""SNS自動化の共通処理：設定・アカウント・予約キュー・予算台帳の読み書き。"""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
ACCOUNTS_DIR = ROOT / "accounts"
QUEUE_DIR = ROOT / "queue"
USAGE_DIR = ROOT / "usage"
JST = timezone(timedelta(hours=9))

# 二十四節気のおおよその開始日（年によって±1日ずれる）
SEKKI = [
    (1, 5, "小寒"), (1, 20, "大寒"), (2, 4, "立春"), (2, 19, "雨水"),
    (3, 5, "啓蟄"), (3, 20, "春分"), (4, 4, "清明"), (4, 20, "穀雨"),
    (5, 5, "立夏"), (5, 21, "小満"), (6, 5, "芒種"), (6, 21, "夏至"),
    (7, 7, "小暑"), (7, 22, "大暑"), (8, 7, "立秋"), (8, 23, "処暑"),
    (9, 7, "白露"), (9, 22, "秋分"), (10, 8, "寒露"), (10, 23, "霜降"),
    (11, 7, "立冬"), (11, 22, "小雪"), (12, 7, "大雪"), (12, 21, "冬至"),
]


def now_jst():
    return datetime.now(JST)


def sekki_for(day):
    current = SEKKI[-1][2]  # 1月5日より前は冬至
    for month, d, name in SEKKI:
        if (day.month, day.day) >= (month, d):
            current = name
    return current


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=1000)


def load_config():
    return load_yaml(ROOT / "config.yaml")


def load_accounts():
    accounts = {}
    for path in sorted(ACCOUNTS_DIR.glob("*.yaml")):
        acc = load_yaml(path)
        accounts[acc["id"]] = acc
    return accounts


def queue_items(base=QUEUE_DIR):
    """予約キュー内の全投稿を (パス, 内容) で返す。"""
    for path in sorted(base.glob("*/*.yaml")):
        yield path, load_yaml(path)


def recent_texts(account_id, limit=20):
    """重複を避けるため、そのアカウントの最近の投稿文を新しい順に返す。"""
    items = [item for _, item in queue_items() if item.get("account") == account_id]
    items.sort(key=lambda item: item["scheduled_at"], reverse=True)
    return [item["text"] for item in items[:limit]]


# ---- 予算台帳 -------------------------------------------------------------

# 100万トークンあたりの米ドル（入力, 出力）。表にないモデルは高めに見積もる。
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
UNKNOWN_PRICE = (10.00, 50.00)


def ledger_path(month=None):
    month = month or now_jst().strftime("%Y-%m")
    return USAGE_DIR / f"{month}.json"


def load_ledger():
    path = ledger_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"usd": 0.0, "calls": 0}


def save_ledger(ledger):
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cost_usd(model, input_tokens, output_tokens):
    price_in, price_out = PRICES.get(model, UNKNOWN_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def worst_case_usd(max_tokens, max_input_tokens=5000):
    """1回の呼び出しで起こりうる最大の費用（表にない高価なモデルに切り替わった場合も含む）。"""
    return cost_usd("unknown", max_input_tokens, max_tokens)
