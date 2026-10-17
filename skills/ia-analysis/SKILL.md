---
name: ia-analysis
description: 情報設計の分析段階（Analyze）。課題説明と資料から、設計対象・利用者・目的・利用環境を整理した問題定義、コンテンツインベントリ、情報監査（重複・欠落・矛盾・粒度の不一致、検証可能な仮説、追加調査事項）を作る。分類体系や画面構造は確定せず、ia-taxonomy・ia-structureへ渡す。
metadata:
  version: 0.1.0
  contract_version: "1.0"
---

# ia-analysis

依頼の問題を定義し、対象コンテンツを入力の位置付きで一覧にする。分類体系、ラベル、画面構造は決めない。ここで見つけた課題は、担当する段階へ渡す材料として記録する。

記録の共通規則は [references/records.md](references/records.md)、入力の形式と位置子は [references/input-formats.md](references/input-formats.md) にある。

## 必須入力

- 課題の説明（依頼文、ヒアリングの回答）。
- 対象資料（ページ一覧、既存資料、CMSエクスポートなど）、または資料が未提供である旨。

入力は `inputs.yaml` に登録し、取込み結果 `ingest/<input_id>.yaml` がある状態から始める。登録されていない資料を読んだ場合は、先に登録する。

## 使う知識

| 知識 | 使う場面 |
|---|---|
| KN-007 利用者、コンテンツ、文脈による問題定義 | 手順1・2。設計対象・利用者・目的・利用環境の整理と、構造の採用理由への接続 |
| KN-008 コンテンツ分析とコンテンツ一覧 | 手順3・4。インベントリの作り方と、配置済み・未処理・対象外の区分 |

知識本文はKN-IDで探す（リポジトリでは `library/index.yaml` の `path`、配布物では同梱の参照資料）。読んだ知識は `iakit run knowledge` で記録する。

## 手順

### 1. 判定に要る4項目を確かめる

設計対象、利用者、目的、利用環境を、入力の位置とともに抜き出す。資料にない項目は推測で埋めない。ia-orchestratorから実行している場合は、不足項目の質問と仮定の扱いはia-orchestratorの手順に従う。単独で実行している場合は、不足項目を5項目以内でユーザーに質問し、回答がなければ仮定として判断に記録する。

### 2. 問題定義を書く

[templates/problem-definition.md](templates/problem-definition.md) に沿って `problem-definition.md` を書く。各項目には入力の位置子か、仮定を記録した判断（`IA-nnn rN`）を付ける。利用者の声や不満は、入力に書かれた範囲で引く。

課題の種類の判定や、仮定を置くといった判断は `decisions.yaml` に記録する（`stage: analyze`）。

### 3. コンテンツインベントリを作る

対象資料の各コンテンツを `content-inventory.yaml` に登録する。

- `content_id` は `CONTENT-001` から順に付ける。ラベルを識別子にしない。
- `input_id` と `locator` には、そのコンテンツを確認した入力と取込み結果の位置子を書く。
- `title` は資料の表記をそのまま写す。
- 資料が未提供の場合は `items: []` とし、必要な収集項目を情報監査に書く。架空の既存コンテンツを登録しない。

### 4. 区分と理由を付ける

各項目の `status` を決める。

- `placed`: 設計の対象に含め、後の段階で分類・配置する。`reason` は `null`。
- `excluded`: 設計の対象外。依頼の目的との関係から理由を書く（例: 利用者のタスクに使わない、別の見直しで扱う）。
- `unprocessed`: 対象に含めるか決められない。決めるのに必要な情報と、確認先を理由に書く。

対象外と未処理の判断が利用者の目的に影響する場合は、判断として記録する。インベントリの区分は後の段階でも変えられる。その場合はインベントリの版を上げて登録し直す。

### 5. 情報監査を書く

[templates/information-audit.md](templates/information-audit.md) に沿って `information-audit.md` を書く。入力を比べて次を探し、それぞれ対象のIDと入力の位置を付ける。

- 重複: 同じ内容のコンテンツ、同じ意味の複数の語。
- 欠落: 目的に要るのに資料にないコンテンツや属性、空の属性値。
- 矛盾: 資料どうしで説明が食い違う箇所。どちらが正しいかはこの段階で決めず、後の段階の判断材料として両方の位置を残す。
- 粒度の不一致: 同じ種類の項目で、扱う範囲や詳しさがそろっていないもの。
- 同名異義語の候補: 同じ語が異なる意味で使われている箇所。分けるかどうかはia-taxonomyが決める。

情報ニーズと探索行動について、検証可能な仮説を書く。仮説は「〜という利用者は〜から探し始める」のように、確かめる方法が決められる形にする。実ユーザー調査をしていないため、仮説を事実として書かない。不明点と追加調査事項も一覧にする。

### 6. 登録して検査する

3つの成果物を登録する。

```text
uv run --locked iakit run artifact runs/<run_id> content-inventory.yaml --kind content_inventory
uv run --locked iakit run artifact runs/<run_id> problem-definition.md --kind problem_definition --depends <使った入力ID>
uv run --locked iakit run artifact runs/<run_id> information-audit.md --kind information_audit --depends <使った入力ID> <インベントリのART-ID>
```

`uv run --locked iakit contracts check runs/<run_id>` に通るまで直す。

## 次の段階へ渡すもの

問題定義（ART-IDと版）、インベントリ（ART-IDと版）、情報監査で見つけた同名異義語・同義語・矛盾の候補を、ia-taxonomyへ渡す。分類や構造の案を思い付いた場合も、ここでは確定せず、情報監査の「後の段階への申し送り」に書く。
