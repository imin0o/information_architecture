# 情報設計AIエージェント Skill群

要件の正本は [requirements.md](requirements.md)、実装順と残件は [roadmap.md](roadmap.md)、判断・検証履歴は [milestones.md](library/build-logs/milestones.md) を参照する。

## 実行環境

構築用CLI `iakit` はリポジトリのcheckoutからeditable installして使う。wheel単独での利用・`uv tool install`は対応対象に含めない。CodexとClaude Codeのどちらも同じcheckoutとコマンドを使う。ホストによるSkillの発見・配置の確認はM1で行う。

- Python下限は3.12。通常開発は `.python-version` の3.14を使う。
- 検証用uvは0.12.7、ビルドバックエンドは `uv_build==0.12.7`。Pythonのパッチ版とuvの実版は検証記録に残す。
- `uv.lock` に従い `--locked` で実行する。`--frozen` はlockと設定の整合確認を省くため、標準の検査手順には使わない。

PowerShellとGit Bashで共通の手順:

```sh
uv sync --locked
git config core.hooksPath .githooks
uv run --locked pytest -q
uv run --locked iakit check --local
```

Git検査はindexを見る。新規ファイルが未ステージなら検査対象に入らず、必要な登録簿もindexにない場合は失敗する。ステージ前にM0成果物全体を確認する場合は、次のrunnerを使う。

```sh
uv run --locked python scripts/verify.py --report library/build-logs/checks/m0-review.yaml
```

runnerはPython 3.12・3.14の一時環境で回帰テストを実行し、`.gitignore`で除外されない追加・変更・削除を別の一時indexに反映して `iakit check --local` を実行する。実indexは変更しない。検証用環境・テスト出力・indexは終了時に回収する。失敗時は非ゼロで終了し、その実行の成功記録を作らない。原典はローカルでハッシュ照合するだけで送信しない。

## Git追加・外部送信前の検査

`pre-commit` はindex全件を検査する。登録簿・自作素材の許可リストも同じindexから読み、欠落・不正な登録簿を合格扱いしない。自作素材の登録簿は生成器との一致も必要で、ハッシュの任意追記では許可されない。

`pre-push` は送信予定の参照を標準入力から読み、先端と送信予定コミットを検査する。一度追加して後で削除した機密ファイルも対象になる。新規ブランチはローカルで把握している送信先のremote-tracking参照から到達できない履歴を検査する。古い参照しかない場合は余分に検査することがある。先端の登録簿は必須。M0より前の、両登録簿が存在しないコミットには先端の原典ハッシュを適用し、自作素材の例外は認めない。登録簿が片方だけ存在する場合や、不正な場合は停止する。

履歴内の自作素材は、各コミットの登録簿と実ファイルのハッシュを照合する。登録簿は現行生成器の出力、または確認済みの旧版登録簿全体の固定ハッシュと一致する場合だけ認める。旧版の許可はコミット検査だけに適用し、index・配布ディレクトリでは現行生成器との一致を要求する。過去の生成器コードは実行しない。

生成器を更新するときは、更新前に `uv run --locked iakit fixtures check` がpassすることを確認し、その `expected_catalogue_sha256` を `scripts/iakit/fixtures.py` の `HISTORICAL_CATALOGUE_HASHES` に残す。更新後は素材・登録簿を再生成する。履歴から未確認のハッシュを自動収集したり、任意のファイルを旧版として追加したりしない。

ローカル検査の対象は次のとおり。

- 登録原典と同一ハッシュ、禁止された原典・実行記録・生ログのパス。
- 未許可の書籍拡張子、PDFヘッダー、ZIP内のEPUB `mimetype`。一般のZIPはEPUB扱いしない。
- JSON・YAML・Markdown front matterの機密区分。配列・入れ子・引用符付きキーを含む。
- 構造化メタデータの解析失敗。JSON・YAMLの重複キーも拒否する。その他のテキストでは区分行の正規表現も補助に使う。

`git commit -a` でもフックは動作する。`--no-verify`、フック設定の無効化、別のGit操作では回避できるため、誤操作防止の検査であり、任意の操作を強制的に制限する仕組みではない。

GitHub Actionsの定義は `.github/workflows/checks.yml`。Python両版でlock整合・回帰テスト・index全件を検査する。原典をアップロードせず、原典再照合は `not_run` とする。外部CIは送信後の確認であり、送信前検査の代わりにはならない。CIの実行・必須チェック設定・公開範囲の変更はローカル実装とは別の操作で、今回実施していない。

## 配布前の検査と残る制約

```sh
uv run --locked iakit leaks check --dir dist
```

指定先が存在しない・ディレクトリでない・空の場合は失敗する。圧縮前の配布ディレクトリを検査する。ZIP・wheel・tar等の内部を網羅する検査ではないため、M5では検査済みの内容から配布物を作り、最終配布物との対応を記録する。

配布ディレクトリではSkill名やbundle名の下も検査し、どの深さでも `sources/`（`manifest.yaml`以外）、`runs/`、`library/build-logs/raw/` の混入を拒否する。

`.gitignore` は `sources/manifest.yaml` を残して原典・抽出物を除外し、`runs/`、生ログ、Python環境・キャッシュ、`dist/`、`build/`、`.tmp/` も除外する。除外は既存の追跡ファイルや `git add -f` には効かないため、漏えい検査も併用する。

`.gitattributes` はテキスト・フックをLF、PDF・EPUB等をバイナリに指定する。Windowsの `core.autocrlf` 設定が有効でも、自作素材のバイト列やシェルの先頭行を変換しない。

部分コピー・翻訳・要約・ラベルのない機密本文をすべて検出することはできない。原典由来の部分一致検出はM1で抽出結果ができてから評価し、機密区分の継承はM2・M4で入力登録と派生物の契約に組み込む。ハッシュや形式検査を本文の公開許可と解釈しない。

## 書誌と抽出結果

`candidate` は推定値、`bibliography` は人が原典で確認した値として分離する。書誌の確認者・確認日時は組で記録する。`registered` 以降には確認済みの題名・著者も必要であり、ISBN等は確認できるまでnullでよい。日時は時差付きのRFC3339表記とする。これらは記録の整合確認であり、人の確認そのものを自動化しない。

M1では `BOOK-ID + 原典SHA-256 → extraction_id + 抽出テキストSHA-256 + 抽出条件 → locations.yaml` を結ぶ。対応表にも抽出ID・対象テキストハッシュを記録し、再抽出には新IDを割り当てる。書誌未確認の試験抽出は書誌確認済み状態と分けて扱う。複数形式・異版を併存させる際はBOOKと原典ファイルを一対多に拡張する。

```sh
uv run --locked iakit extract run --book BOOK-003          # sources/extracted/EXTRACT-003-<連番>/ を作る
uv run --locked iakit extract run --fixture pdf/page-labels.pdf --root .tmp/extracted
uv run --locked iakit extract check --local                # 対応表・ハッシュと原典の照合
uv run --locked iakit excerpt ref --extraction sources/extracted/EXTRACT-003-001 --lines 120-124
```

出力は抽出ID・ハッシュ・件数・位置の候補だけで、本文を含まない。上流book-to-skillとの差分と採用条件は [adapters/book-to-skill/README.md](adapters/book-to-skill/README.md) に記録する。

ホストでのSkill配置は `uv run --locked iakit hosts probe --host claude --host codex` で確認する。自作の `tests/host-compat/iakit-host-probe/` を一時ディレクトリに置き、各ホストのCLIへ質問を送る。終了時に一時ディレクトリを削除する。
