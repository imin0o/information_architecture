# 情報設計AIエージェント Skill群

要件の正本は [requirements.md](requirements.md)、実装順と残件は [roadmap.md](roadmap.md)、判断・検証履歴は [milestones.md](library/build-logs/milestones.md) を参照する。

## 実行環境

構築用CLI `iakit` はリポジトリのcheckoutからeditable installして使う。wheel単独での利用・`uv tool install`は対応対象に含めない。CodexとClaude Codeのどちらも同じcheckoutとコマンドを使う。ホストによるSkillの発見・配置はM1で確認した。

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

`pre-push` は漏えい検査の後に `iakit check --pre-push` を実行する。標準入力の送信予定コミットを、一時checkoutでそれぞれ `iakit check --local`（CIと同じ全項目。配布検査を含む）に通し、失敗すれば送信を中止する。作業ツリー・indexの未コミットの修正では送信対象の不備を隠せない。同じコミットは1回だけ検査し、参照の削除は対象外とする。一時checkout・実行環境は終了時に削除する。ブランチ保護は使わず、この送信前検査を必須の検査とする。回帰テストは送信前に `scripts/verify.py` で実行する。

GitHub Actionsの定義は `.github/workflows/checks.yml`。Python両版でlock整合・回帰テスト・index全件を検査する。原典をアップロードせず、原典再照合は `not_run` とする。外部CIは送信後の確認であり、送信前検査の代わりにはならない。

## 配布物の生成と検査

```sh
uv run --locked iakit dist build                 # dist/<skill>/ を生成し、verification/distributions.yaml を更新する
uv run --locked iakit dist check                 # 一時ディレクトリで再生成し、記録と照合する（iakit check・pre-push にも含む）
uv run --locked iakit dist check <配置先>        # 移設・配置した配布物の検査
uv run --locked iakit dist diff <旧> <新>        # 配布IDとファイルの差分
uv run --locked iakit run compare <実行A> <実行B> # 判断・Skill・知識の差分（NFR-02）
```

受け入れ検査（M6）は、`dist/` をビルド元の外の一時ディレクトリへ各ホストの配置先（Claude Code: `.claude/skills`、Codex: `.agents/skills`）として写して行う。ホストの実行ファイルは `IAKIT_CLAUDE_BIN`・`IAKIT_CODEX_BIN` で指定できる。ホストにはビルド元を指す環境変数を渡さない。

```sh
uv run --locked iakit accept placement                       # 同梱の実行時処理だけで配置・開始・再開・停止条件を検査する
uv run --locked iakit accept host --host claude|codex --repeat 2   # ホストのモデルで発見・単独利用・依存欠落などを検査する
uv run --locked iakit accept route --host H --case webia-001 --run-id RUN-...  # 代表ケースを実行して runs/ へ回収する（--refine で判定後のRefine）
```

`dist build` は5つのSkillを新しい作業ディレクトリに生成し、検査に通った場合だけ `dist/` を丸ごと入れ替える。旧ディレクトリへの差分コピーはしないため、削除した参照資料は残らない。検査に失敗した場合は既存の `dist/` を変更しない。入れ替えには固有の退避先を作り、配置に失敗したら元に戻す。復元も失敗した場合は旧配布物を退避先に保持し、その場所をエラーに示す。

各Skillには、SKILL.mdと付属ファイルに加えて、本文が参照するKN-IDの知識本文・索引・用語・矛盾記録、主張と照合状態（`library/runtime-evidence.yaml`）、使う成果物のスキーマとその参照先を同梱する。用語・矛盾記録が参照する主張と、その出典・照合記録も含める。ia-orchestratorは実行時処理を持つため、文献ノート以外の全知識と全スキーマを同梱する。文献ノート、原典、抽出テキスト、抜粋は同梱しない。Markdownの相対リンクが配布物の中で解決しなければ生成を止める。

配布対象の知識本文には現在の内容に結び付いた有効な照合・レビュー記録を要求し、同梱する主張はすべて `verified`・`supported` でなければ生成を止める。本文を変更して照合が無効になった場合は、再レビューまで新しい配布候補を作れない。仮説を扱う開発用ランタイムと、配布候補の完了検査は区別する。

生成の順序は、同梱ファイルの確定、対象に結び付いた照合記録（`verification/bundle-receipt.yaml`）の作成、`build-manifest.yaml` の作成とする。照合記録は同梱ファイルのハッシュと、既存の照合記録（VR-ID）から導いた主張の状態を持つ。原典の再照合はしない。manifestには `requires_skills`（契約版付き）、共有ID（KN・CLAIM・EV・TERM・CONFLICT）の内容ハッシュ、スキーマと同梱ファイルのハッシュ、構築ツールの版を記録し、manifest自身と配布IDを除いた内容から配布ID（`DIST-` + 16桁）を決める。生成日時は記録しない。

同梱する出典ハッシュと照合記録は、同梱する主張に関係する分だけに絞る。無関係な文献や照合記録を追加しても配布IDは変わらない。原典・固定抽出のない環境（CI）でも、同じ配布IDになる。

`dist check <配置先>` と、配布物での `run init` は、manifestと実ファイルの差（欠落・追加・変更）、配布IDの不一致、依存Skillの不足と契約版の不一致、同じ共有IDで内容の異なる配布物の混在を検出する。`run init` では、経路に要らないSkillの欠落は止めない。Pythonが作る `__pycache__` は同梱物として扱わない。

`verification/distributions.yaml` は配布候補ごとの配布IDと照合記録のハッシュを記録する。`iakit check`・CI・pre-pushは、再生成した結果がこの記録と一致しない場合に失敗する。Skill・知識・実行時処理を変更したら `dist build` を実行し、記録を一緒にコミットする。ZIP等に圧縮する場合は、検査した `dist/` から作り、配布IDで対応を確認する。

`run compare` は前提の重要性、根拠の主要・補助の区別、採用理由、不採用理由、反証、検証記録と生成設定の差も出力する。配列の並べ替えだけでは差にしない。

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

ホストでのSkill配置は `uv run --locked iakit hosts probe --host claude --host codex` で確認する。PATH上と別の実行ファイルを使う場合は `IAKIT_CLAUDE_BIN`・`IAKIT_CODEX_BIN` で指定する。自作の `tests/host-compat/iakit-host-probe/` を一時ディレクトリに置き、各ホストのCLIへ質問を送る。終了時に一時ディレクトリを削除する。

ホスト確認の発見・明示利用は、標準出力が期待する`PROBE-RESULT`の1行と一致した場合だけ合格する。標準エラーの診断ログは判定に使わない。結果には`response_check: stdout_exact_line`を記録する。Skillなしの対照では、標準出力にトークンが含まれたら失敗する。

## データ契約と課題ケースの検査

```sh
uv run --locked iakit contracts check                     # tests/fixtures/contracts/ と library/ の登録簿
uv run --locked iakit contracts check runs/RUN-001        # 実行ディレクトリ単位
uv run --locked iakit contracts check --local --extracted sources/extracted runs/RUN-001
uv run --locked iakit cases check                         # tests/cases/ の版固定とルーブリックの網羅
```

`contracts check`はスキーマ（`schemas/`、JSON Schema Draft 2020-12）に通ったファイルだけを整合性検査へ渡す。`source_verification`・`support_status`・`confidence`・`validation_status`は登録簿・照合記録・調査記録から導出し、記録された値が導出値と異なれば失敗する。原典・位置の人による照合は行わないため、結果は常に`human_source_check: not_run`となる。`--local`を付けると、手元の固定スナップショットで抜粋のハッシュ・プレビュー・位置を照合し、スナップショットがない根拠は`not_run`として数える。

実行ディレクトリでは`run.yaml`・`inputs.yaml`・`decisions.yaml`と、実行記録に列挙した成果物の実体を必須にする。完了した標準経路には構造化成果物一式も必要となる。成果物本体のID・版・機密区分を実行記録と照合し、本体が参照する入力・成果物を`depends_on`から省略できない。サイトマップは分類と、概念IDを使う場合の語彙も依存に含める。出典登録簿・照合記録だけのディレクトリは単独で検査できる。

調査は重複しないIDと`research_record`入力への参照を持ち、入力以上の機密区分を継承する。無効・重複した調査は`tested_*`の根拠に使えない。送信許可の`approvals[].input_ids`は必須で、送信する入力・派生元の入力IDすべてが許可対象に含まれる必要がある。旧形式の許可記録には、実際に許可された対象IDを追記してから再検査する。位置対応表が手元にあれば、照合記録の`locations_sha256`とも照合し、不一致の記録を無効にする。対応表がない環境では既存記録の参照確認にとどまり、再照合済みとは扱わない。

`cases check`は入力ファイルのハッシュ、ルーブリック全項目への合格条件か非該当理由の記載、境界条件ごとの検査関数の実在を確かめる。設計内容の合否は人がルーブリックで判定し、この検査では判定しない。

`iakit review sheet`は未確認の根拠と、新たな知識本文にまだ確認が結び付いていない根拠を確認シートに載せる。判定は`iakit review record --by <名前>`に`--all-ok`または個別の指定を付けて記録する。個別の`--sample-issue`だけでは他の品質確認を合格にせず、未登録のID・確認種別や不正な指定があれば、どの確認記録も変更しない。

`--claim`・`--reject`・`--sample-issue`は確認済みの対象にも使える。照合記録は追記して履歴を残し、同じ根拠の原典照合状態には追記順の最新の有効記録を使う。後から却下した根拠を、過去の合格だけで検証済みに戻さない。

## Skillと実行ディレクトリ

MVPの5つのSkillのソースは `skills/` にある。入口は `ia-orchestrator`（経路は `skills/ia-orchestrator/routing.md`）で、`ia-analysis`・`ia-taxonomy`・`ia-structure`・`ia-evaluation` のSKILL.mdを順に読んで実行する。各Skillは手順、テンプレート、記録規則（`references/records.md`、各Skillに同じ写しを置く）を持ち、知識は `library/index.yaml` のKN-IDで参照する。配布物への同梱は `iakit dist build` が行う。

```sh
uv run --locked iakit run init runs/RUN-001 --run-id RUN-001 --task-type web_ia --host claude-code --model <モデル名>
uv run --locked iakit input add runs/RUN-001 <ファイル> --kind page_list --description <説明> [--classification internal]
uv run --locked iakit input review runs/RUN-001 INPUT-002 --outcome ignored  # 命令文の候補を確かめた記録
uv run --locked iakit run knowledge runs/RUN-001 KN-007 KN-008      # 段階で読んだ知識の内容ハッシュ
uv run --locked iakit run artifact runs/RUN-001 content-inventory.yaml --kind content_inventory
uv run --locked iakit run event runs/RUN-001 refine --note FIND-001  # Refineの巡数を進める
uv run --locked iakit run event runs/RUN-001 completed               # 契約検査に通らなければ終了しない
uv run --locked iakit run resume runs/RUN-001                        # 再開前のハッシュ照合
```

`input add` は入力を `inputs/` に固定し、位置付きの取込み結果 `ingest/<input_id>.yaml` を作る。対応形式はテキスト、Markdown、PDF、DOCX、CSV、XML、JSON、YAML、SKOS（RDF/XMLのみ）で、位置子と読めない部分の扱いは `skills/ia-orchestrator/references/input-formats.md` に書いた。書籍の固定抽出（`iakit extract`）とは別の処理で、印刷ページ番号は推測しない。DOCTYPE・実体宣言を含むXMLは解析しない。資料内の命令文の候補は `instruction_like` として示すが、従わない。候補は `input review` で位置ごとに確かめた結果（命令ではない・従わない・送信の要求を止めた）を記録し、記録のない候補が残る実行は `contracts check` に通らない。

取込み結果全体の内容ハッシュは `inputs.yaml` の `ingest_sha256` に固定する。再開・契約検査では、スキーマと入力ID・形式・実行IDとの対応に加え、このハッシュを照合する。旧実行の取込み結果にハッシュがない場合や内容が変わった場合は、固定した入力を `input add DIR FILE --replace INPUT-nnn --kind KIND --description TEXT` で再登録する。同じ原本でも取込み結果が変われば、命令候補の確認をやり直し、依存成果物を `stale` にする。JSONの重複キーは全階層で解析失敗として記録し、XMLの混在テキストは子要素の後の本文も位置付きで取り込む。

`run artifact` は成果物のハッシュ・版・依存・機密区分を記録する。依存は本体の参照から導き、本体から読めない依存は `--depends` で渡す。依存先が変わると後続の成果物を連鎖的に `stale` にする。`contracts check` は、完了した経路の全段階の成果物、Markdown成果物の最初の節「## 仮定に基づく箇所」・型ごとの必須見出し・冒頭の機密区分と実行記録との一致・判断参照の版、評価の指摘（`findings.yaml`）とRefineの整合、取込み結果と入力のハッシュの対応も検査する。コード例の中の見出し・機密区分は成果物の記載として数えない。未解消の `critical` は、ユーザーが残置を決めた `accepted` も完了を妨げる。

検査レポートのスキーマエラーは、位置・違反した制約・スキーマの期待値だけを記録する。YAML解析エラーも例外の種類と行・列に限定し、入力値や本文断片を含めない。分野Skillに同梱する形式検査も同じ方針に従う。
判断を参照する成果物は、その判断の観察元と調査記録の入力にも依存し、機密区分を引き継ぐ。評価一式は指摘がなくても全設計成果物に依存する。必要な依存を持たない既存の記録は `refresh` で `stale` にし、確認して再登録する。開始時には必要なSkillの `metadata.contract_version` を実行時処理の契約版と照合し、非互換・未指定なら開始しない。

人の操作を待たずに進める構造にしている。質問の回答がなければ仮定で進め、固定ケースの内容の合否（AC-09）は、成果物を作ったエージェントとは別の確認エージェントがルーブリックで判定する（手順は `skills/ia-orchestrator/references/independent-review.md`）。判定の記録は次のコマンドで検査する。自己評価（実行と同じhost・model）は拒否する。

```sh
uv run --locked iakit run judgment runs/RUN-001 --case webia-001
```

配布物では、`iakit.runtime.bundle` が実行時処理（`scripts/run_iakit.py` と必要なモジュール、スキーマ、固定ケースとルーブリック、知識の索引と本文）をia-orchestratorに同梱し、`uv run <ia-orchestrator>/scripts/run_iakit.py run ...` で同じコマンドを使える。
文献根拠の検査には、構築時に出典登録簿と照合記録から導出した `library/runtime-evidence.yaml`（主張・適用条件・照合状態）を同梱する。原典本文・抜粋・抽出パス・文献ノートは含めない。`registered-sources.yaml` でこの情報のハッシュを固定し、同梱知識の変更・欠落も検査する。`runtime-evidence.yaml` は、同梱する根拠に結び付いた照合記録のIDと内容ハッシュを持つ。配布先で原典照合を再実行したとは扱わない。
