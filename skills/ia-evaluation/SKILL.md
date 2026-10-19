---
name: ia-evaluation
description: 情報設計の評価段階（Validate）。設計成果物の版を対象に、ヒューリスティック評価、構造・参照整合性の検査、主要タスクの経路の点検を行い、指摘（ID、重大度、対象版、根拠、推奨修正、解消条件）と検証計画を出力する。Refineの再評価もここで行う。設計成果物の修正は担当Skillへ戻す。
metadata:
  version: 0.1.0
  contract_version: "1.0"
---

# ia-evaluation

設計成果物を評価し、問題を指摘として記録する。成果物そのものは直さない。修正は指摘の `return_to` が示す段階の担当Skillが行い、このSkillは再評価で解消を確かめる。

記録の共通規則は [references/records.md](references/records.md) にある。

## 必須入力

- 評価対象の成果物とその版（`run.yaml` の `current` の成果物）。
- 問題定義（利用者、目的、主要タスクと到達先の条件）。
- 利用可能な根拠: 判断記録、出典登録簿と照合記録、ユーザーが提供した実ユーザー調査の記録（`observations.yaml`）。
- 案件に固定の評価基準（ルーブリック）がある場合はその版。

`stale` の成果物は評価しない。先に再生成を求める。評価対象か問題定義が足りなければ、評価も指摘の記録も行わず、足りない入力と作成を担当するSkillを示して止める。

## 使う知識

| 知識 | 使う場面 |
|---|---|
| KN-010 構造とラベルの検証方法 | 手順4・6。ツリーテスト、カードソートなどの適用可否と検証計画 |
| KN-005 ラベルの命名と粒度の一貫性 | 手順2。命名と粒度のヒューリスティック |

## 評価の制約

- MVPで必須の評価は、ヒューリスティック評価、構造・参照整合性の検査、主要タスクの経路の点検、検証計画の作成である。LLMの模擬カードソートなどは必須にしない。
- LLMによる模擬評価を行った場合は、結果を `simulated` として扱い、実測と混同しない。推定した認知負荷や発見可能性を実測のスコアとして示さない。
- 専門家の点検や、このSkillの評価だけを根拠に判断を `tested_*` にしない。`tested_*` はユーザーが提供した調査記録がある場合だけである。
- 固定ルーブリックがある場合は、項目と合格条件を変えずに観点として使い、`findings.yaml` の `rubric_version` に版を書く。ルーブリックによる合否は、成果物を作ったエージェントとは別の確認者（指定した確認エージェントなど）が判定する（手順は ia-orchestrator の references/independent-review.md にある）。このSkillは合格と書かず、判定を待って止まらない。

## 手順

### 1. 機械検査を実行する

`uv run --locked iakit contracts check runs/<run_id>` を実行する。失敗は、構造・参照の問題として指摘にする（例: 到達できないノード、未配置のコンテンツ、判断の状態の不一致）。コマンドを使えない環境では、ia-orchestratorの点検項目で確かめ、機械検査は `not_run` と書く。

ia-orchestratorを使わずに単独で実行していて `iakit` を使えない場合は、`uv run <このSkillのディレクトリ>/scripts/check_artifacts.py <成果物のディレクトリ>` で、同梱したスキーマによる形式検査を行い、通るまで直す。この検査は参照や依存の整合を確かめないため、報告には形式検査の結果を書き、契約検査は `not_run` とする。

### 2. ヒューリスティック評価を行う

次の観点で成果物を読む。固定ルーブリックがある場合は、その項目（R-01〜）を観点に対応付ける。

| 観点 | 確かめること |
|---|---|
| 発見可能性 | 主要タスクの到達先へ、利用者が選ぶ語でたどれるか |
| 理解可能性 | ラベルと `scope_note` で意味を区別できるか。同名異義語が1つのメニューにまとまっていないか |
| 一貫性 | 兄弟項目、同じファセット内の項目の観点と粒度がそろっているか |
| 網羅性 | 全コンテンツが配置済み・未処理・対象外に区分され、理由があるか |
| 認知負荷 | 選択肢の数や経路の長さが主要タスクの妨げにならないか（推定であり実測ではない） |
| 拡張性 | 新しいサービスや地区を加えたときに構造が保てるか |
| 追跡可能性 | 判断が観察・文献根拠・仮定を区別し、成果物から判断のIDと版をたどれるか |

### 3. 主要タスクの経路を点検する

問題定義の主要タスクごとに、サイトマップの経路（タクソノミーの経路では、語から概念への対応）をたどり、到達先の条件を満たすかを確かめる。別の属性から入る経路も試す。

### 4. 指摘を記録する

`findings.yaml` に指摘を書く（スキーマ `findings.schema.json`）。ファイルをテキスト編集せず、1件ずつ次のコマンドで追加する。コマンドは `finding_id`（`FIND-001` から順）、`round`（現在の巡数）、`status: open` を決め、`target` の `version` を省けば対象の現行版を補う。スキーマや指摘ごとの整合性に合わなければ書き込まない。再評価で同じ問題を見つけた場合は新しく追加せず、既存の指摘の状態を更新する。

```text
uv run --locked iakit finding add runs/<run_id> <ファイル|-> | --json '<JSON>' [--rubric-version <版>]
```

- `severity`: `critical` は主要タスクを妨げる問題、根拠の不正な断定（未照合・未レビュー・`not_supported` の主張や未実測の内容を確定事項として扱う）、機密情報の不適切な扱い。`major` は局所的な誤解や不整合。`minor` は意味を変えない表記上の問題。
- `target`: 指摘した成果物のART-IDと版。`locator` に成果物内の位置（ID、見出し）。
- `basis`: ルーブリック項目、検査結果、判断・入力・主張のID。
- `recommendation`・`resolution_condition`: 推奨する修正と、解消したと判定できる条件。
- `return_to`: 修正を担当する段階。
問題が見つからない観点も、ia-review.mdに確かめた範囲を書く。指摘がない場合も `findings.yaml` を `findings: []` で作る（`iakit finding add` を使わずに作る場合は、本体の版と機密区分は登録時にコマンドが書き換える）。

### 5. 評価報告を書く

[templates/ia-review.md](templates/ia-review.md) に沿って `ia-review.md` を書く。指摘の一覧（重大度順）、観点ごとの結果、改善の優先度と修正理由、機械検査の結果、ルーブリックの観点との対応を示す。

### 6. 検証計画を書く

[templates/validation-plan.md](templates/validation-plan.md) に沿って `validation-plan.md` を書く。未検証の判断（`not_tested`）と仮定ごとに、確かめる方法（ツリーテスト、カードソート、検索ログの確認、聞き取りなど）、対象タスク、成功の基準を対応付ける。計画では判断を `IA-nnn rN` で示す。`not_tested` のまま計画に対応付けてよい。`validation_status: planned` にする場合は判断の新しい版を作り、その判断を参照する成果物も新しい版に更新する（Refineで判断を改訂するときにまとめてよい）。`planned` の判断は必ず計画に載せる。

### 7. 登録して検査する

```text
uv run --locked iakit run artifact runs/<run_id> findings.yaml --kind review_findings
uv run --locked iakit run artifact runs/<run_id> ia-review.md --kind ia_review --depends <findings のART-ID>
uv run --locked iakit run artifact runs/<run_id> validation-plan.md --kind validation_plan --depends <問題定義のART-ID>
uv run --locked iakit run refresh runs/<run_id>
```

`refresh` は、未解決の重大な指摘を `run.yaml` の `refine.unresolved_critical` に同期する。
評価一式の登録時には、指摘の有無によらず、登録済みの全設計成果物への依存を自動で付ける。評価後に設計成果物を変更・追加した場合は、評価一式も `stale` になるので再評価する。

## 再評価（Refine）

担当Skillが修正した後に呼ばれた場合は、次を行う。

1. 修正された成果物の新しい版を対象に、手順1〜3をやり直す。
2. 解消した指摘は `iakit finding resolve runs/<run_id> FIND-nnn` で `resolved` にする（`resolved_in` には対象の現行版が入る。別の版で確かめた場合は `--version N`）。解消していなければ `open` のままにする。新たに見つけた問題は `iakit finding add` で追加する。
3. 2巡目の再評価でも残る重大な指摘は `iakit finding resolve runs/<run_id> FIND-nnn --status unresolved --note <要点>` にし、ia-review.mdの「未解決の指摘」にユーザーが判断するための情報（影響、選択肢、必要な追加情報）を書く。
4. findings.yaml、ia-review.md、validation-plan.mdを登録し直す（版はコマンドが上げる）。内容を変えない成果物は `--no-impact <理由>` で再登録する。
