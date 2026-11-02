# TANUKI POLICY ARCADE

Rulease を「業務ルール管理画面」ではなく、**運命判定マシン**として遊ぶデモサイトです。

ユーザーが年齢・信頼度・混沌度・購入額などを操作すると、Web アプリが Rulease v2 の `POST /v2/decisions` を呼び、`ALLOW / DENY`、理由、decision ID、revision、評価時間をゲーム風に表示します。

## 仕組み

```text
Browser
  │ POST /api/decide
  ▼
Policy Arcade (FastAPI)
  │ Bearer service-account key
  │ X-Rulease-Workspace
  ▼
Rulease /v2/decisions
  ▼
Released immutable revision
```

ブラウザに Rulease の API キーを渡さないため、FastAPI が server-side proxy になります。Rulease が未設定のときは UI 確認用の **DEMO MODE** で動き、画面右上に明示されます。

## 1. Rulease を用意

Rulease 側で初期化します。`development` は `rulease init` の既定では承認 0 件なのでデモに向いています。

```bash
rulease init --workspace team --project arcade --username owner
rulease serve --port 8001
```

別ターミナルで、このリポジトリから policy・revision・release・service account key を作成します。

```bash
python scripts/bootstrap_rulease.py \
  --url http://127.0.0.1:8001 \
  --workspace team \
  --project arcade \
  --username owner
```

表示された `RULEASE_*` を `.env` または実行環境の Secret に設定してください。API キーは再表示できない前提で扱ってください。

> `bootstrap_rulease.py` は新規環境向けです。同名 policy がすでに存在する場合は、Rulease の revision/deployment フローで更新してください。

## 2. Web アプリを起動

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export RULEASE_URL=http://127.0.0.1:8001
export RULEASE_API_KEY='rk_...'
export RULEASE_WORKSPACE=team
export RULEASE_PROJECT=arcade
export RULEASE_POLICY=admission
export RULEASE_ENVIRONMENT=development

uvicorn app:app --reload --port 8000
```

`http://127.0.0.1:8000` を開きます。

## Policy

実際のルールは [`policy/arcade.rulea`](policy/arcade.rulea) にあります。

- `arcade.vip_gate` — 20歳以上 / trust 70以上 / BANなし
- `arcade.coupon_drop` — 5,000円以上 / loyalty 3以上 / risk 60未満
- `arcade.midnight_license` — 18歳以上 / 夜型 / chaos 80以下 / BANなし

UI の条件判定は本番モードでは JavaScript に書かれていません。最終判定は Rulease の released revision が行います。

## Docker

```bash
docker build -t policy-arcade .
docker run --rm -p 8000:8000 --env-file .env policy-arcade
```

## Notes

Rulease v2 は alpha foundation なので API contract は今後変わる可能性があります。このデモは現在の `/v2/decisions`、service-account key、workspace header に合わせています。
