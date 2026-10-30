# SNS 自動投稿（Threads）

まこ・りこ・ましろ・れいか の Threads に、承認済みの投稿を予定時刻に自動で出す仕組みです。

## お金について（最優先のルール）

**課金が発生することは、オーナーの承認なしには一切しない。何もなければ課金しない。**

| 使うもの | 費用 | 状態 |
|---|---|---|
| Threads API | 無料 | 使う |
| GitHub Actions | 無料（公開リポジトリのため） | 使う |
| 投稿案づくり（Claude Code に頼む） | 今の Claude の契約の範囲内（追加の請求なし） | 使う |
| 投稿案の自動生成（Claude API） | **有料** | **止めている**（`config.yaml` の予算が 0） |
| X API（X のましろ） | **有料** | **止めている** |

## 流れ

```
投稿案を作る（Claude Code に頼む → PR が届く）
   ↓
あなたが PR を確認：直す／消す／そのまま
   ↓ マージ = 承認
予定時刻に GitHub Actions が Threads に投稿（7時〜23時台、30分ごとに確認）
   ↓
結果がファイルに記録される（status: posted）
```

- 投稿案は `sns/queue/<日付>/<アカウント>-<番号>.yaml` に1投稿ずつ入っています。
- **やめたい投稿**はファイルを削除、**直したい投稿**は `text:` を編集してからマージ。
- 承認が予定時刻より6時間以上遅れた投稿は、季節や時間帯がずれるので投稿せず `expired` にします。
- 投稿に3回失敗すると `error` になり、GitHub からメールで通知が届きます。

⚠️ このリポジトリは**公開**なので、`sns/queue` の投稿案は投稿前から誰でも読めます。
困る場合はリポジトリを非公開にできます（非公開でも月2000分まで Actions は無料。この仕組みは月およそ1000分の見込みです）。

## 最初にやる設定

### 1. Meta の開発者アプリを作る（無料）
1. https://developers.facebook.com/ に、れいか などの Threads とつながっている Instagram アカウントでログインし、開発者登録する
2. 「アプリを作成」→ ユースケースで **「Threads API にアクセス」** を選ぶ
3. 権限に `threads_basic` と `threads_content_publish` を追加する
4. 「役割」→ **Threads テスター** に、まこ・りこ・ましろ・れいか の4アカウントを追加する
5. 各アカウントで Threads を開き、**設定 → アカウント → ウェブサイトのアクセス許可 → 招待** から承認する

アプリは「開発モード」のままで大丈夫です（自分のアカウントだけで使うので審査は不要）。

### 2. アカウントごとのトークンを取る
アプリのダッシュボードの「ユーザートークン生成ツール」で、4アカウント分のトークンを発行します。
**トークンはチャットに貼らないでください。** 次の手順で GitHub にだけ登録します。

### 3. GitHub に登録する
リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で登録：

| 名前 | 中身 |
|---|---|
| `THREADS_TOKEN_REIKA` | れいか のトークン |
| `THREADS_TOKEN_MAKO` | まこ のトークン |
| `THREADS_TOKEN_RIKO` | りこ のトークン |
| `THREADS_TOKEN_MASHIRO` | ましろ のトークン |
| `SECRETS_PAT` | 下の 4. で作るトークン |

### 4. トークンの自動延長用（無料）
Threads のトークンは60日で切れるので、毎月1日と15日に自動で延長します。そのために、GitHub の
**Settings（右上のアイコン）→ Developer settings → Personal access tokens → Fine-grained tokens** で
「Repository access: Reika だけ」「Permissions: Secrets を Read and write」のトークンを作り、`SECRETS_PAT` に登録します。

### 5. Actions の書き込みを許可する
リポジトリの **Settings → Actions → General → Workflow permissions** を **Read and write permissions** にする。

### 6. キャラ設定を書く
`sns/accounts/` の `mako.yaml`・`riko.yaml`・`mashiro.yaml` に人物像・口調・テーマ・NG を書き、`enabled: true` にする。
（`reika.yaml` は占いアプリの内容から下書き済み。確認して直してください）

## ファイル
- `accounts/*.yaml` … アカウントごとの設定（投稿時刻・キャラ・承認ルール）
- `queue/` … 予約中・投稿済みの投稿
- `publish.py` … 予定時刻になった投稿を Threads に出す
- `refresh_tokens.py` … トークンの延長
- `generate.py` … Claude API での自動生成（課金の承認があるまで使わない）
- `usage/` … Claude API の月ごとの使用額（自動生成を使う場合のみ）
