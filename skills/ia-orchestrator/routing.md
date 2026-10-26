# 課題の種類と段階経路

ia-orchestratorが課題の種類から段階を選ぶための表である。要件定義書FR-08の標準経路に従う。段階を黙って省略したり、未対応の種類を対応経路で代用したりしない。

## 課題の種類

| 課題の種類（`task_type`） | 判定の手がかり | 標準経路 | 対応 |
|---|---|---|---|
| Web IA（`web_ia`） | Webサイト・アプリの構造、ナビゲーション、サイトマップ、ページの探しやすさ | Analyze → Classify → Structure → Validate | MVP |
| タクソノミー（`taxonomy`） | 分類体系、統制語彙、同義語、タグ、用語の統一。画面やページの配置は求めていない | Analyze → Classify → Validate | MVP |
| 企業資料・提案書（`corporate_document`） | 資料の章立て、提示順序、見出し | Analyze → Present → Validate | 後続 |
| 展示パネル（`exhibition_panel`） | 展示の構成、パネルの順序 | Analyze → Present → Validate | 後続 |
| コンテンツモデル・CMSスキーマ（`content_model`） | コンテンツタイプ、属性、CMSの項目 | Analyze → Classify → Model → Validate | 後続 |
| デザインシステム（`design_system`） | コンポーネントや表現の体系 | Analyze → Classify → Model → Validate | 後続 |
| RAG向け知識構造（`rag_knowledge`） | 検索拡張生成のための文書分割、メタデータ | Analyze → Classify → Model → Validate | 後続 |

Web IAとタクソノミーの両方に当てはまる場合は、ページの配置や探索経路を求めているかで決める。求めていればWeb IA、語彙と分類だけならタクソノミーとする。既存の調査文書群を製品群や新しい資料に合わせて改訂する依頼は、文書間の用語の区別と製品の分類軸が主題ならタクソノミーとする。章立てや提示順序が主題なら企業資料・提案書（後続）であり、タクソノミーで代用しない。判定に迷う場合は、ユーザーに課題の種類を質問する（質問は他の不足項目と合わせて5項目以内）。

## 段階と担当Skill

| 段階（`stage`） | 担当Skill | 必須入力 | 成果物（`kind`） |
|---|---|---|---|
| Analyze（`analyze`） | ia-analysis | 課題説明、対象資料または未提供である旨 | problem-definition.md（`problem_definition`）、information-audit.md（`information_audit`）、content-inventory.yaml（`content_inventory`） |
| Classify（`classify`） | ia-taxonomy | 問題定義、インベントリまたは対象概念一覧 | controlled-vocabulary.yaml（`controlled_vocabulary`）、taxonomy.yaml（`taxonomy`）、classification-options.md（`classification_options`） |
| Structure（`structure`） | ia-structure | 問題定義、分類・語彙、対象コンテンツ一覧 | information-architecture.md（`information_architecture`）、sitemap.yaml（`sitemap`） |
| Validate（`validate`） | ia-evaluation | 評価対象の版、問題定義、代表タスク、利用可能な根拠 | findings.yaml（`review_findings`）、ia-review.md（`ia_review`）、validation-plan.md（`validation_plan`） |
| Model（`model`） | ia-content-model | 後続 | 後続 |
| Present（`present`） | ia-presentation | 後続 | 後続 |

標準経路の実行には、ia-orchestratorと経路上の段階の担当Skillがすべて必要である。完了時には、経路上の全段階の成果物が登録されていなければならない。

## 戻り先

評価の指摘は、修正を担当する段階へ戻す。

| 指摘の内容 | 戻り先 |
|---|---|
| 問題定義、利用者・目的・利用環境、インベントリの区分 | Analyze |
| 概念の区別、同義語、上位・下位関係、分類の割り当て | Classify |
| 配置、探索経路、ナビゲーションラベル、到達性 | Structure |
| 判断記録の根拠・状態、検証計画 | Validate（または判断を記録した段階） |

戻った段階で成果物を変えた場合は、その後の段階の成果物が `stale` になる。後続の段階も再実行するか、影響がないことを確かめて再登録する。

## UI/UX からの委譲

UI/UX の Skill 群（`ui-architecture`）は、分類、語彙、配置といった上流の判断を変えないと解決しない課題をこのSkillに委ねる。委譲は課題の種類の判定と同じ表で扱い、画面への配置や探索経路を求めていればWeb IA、語彙と分類だけならタクソノミーとする。

IAとUI/UXの推奨が食い違うときは、まずKN-016（文献の適用条件と案件の条件の照合）で両方の適用条件を案件の条件と照らし（UI/UX側は原則カードの `conditions` と `exceptions` で確かめる）、それで決まらなければUI/UXの `knowledge/ontology.yaml` にある `decision_priority`（法令・規格、技術制約、実測、実証研究、設計原則、類推の順）で決める。KN-016の照合手順は同ファイルの「仮説・補完」にある運用手順であり、原典の規範として根拠に挙げない。

### 入口

UI/UX から渡された受け渡し項目のファイルを、依頼の説明として `iakit input add ... --kind brief` で登録する。項目は、元の依頼、モード、許可された範囲、問題の証拠、利用者と主要タスク、対象コンテンツ、維持すべき制約である。

- 許可された範囲は、受け渡し項目に書かれた範囲を上限とする。外部送信の許可は、これまでどおり `run.yaml` の `approvals` に記録したものだけを使う。
- 問題の証拠と対象コンテンツが参照だけの場合は、参照先の資料も入力として登録する。読めなかった参照先は推測で埋めない。
- 課題の判定に要る4項目（設計対象、利用者、目的、利用環境）が欠けていれば、手順4と同じく質問または仮定で補う。

### 返却

終了報告 `report.md` に加えて、次をUI/UXへ返す。成果物の全文は複製せず、成果物への参照一覧と必要な抜粋だけを返す。

| 項目 | 返すもの |
|---|---|
| 実行 | run ID と実行の状態 |
| 成果物 | 成果物IDと版の一覧 |
| 語彙と分類 | 統制語彙（`controlled_vocabulary`）と分類体系（`taxonomy`） |
| 構造 | 構造設計（`information_architecture`）とサイトマップ（`sitemap`）。Web IAの場合だけ返す |
| 未解決の指摘 | `open`・`unresolved` の指摘ID、重大度、状態と戻り先 |
| 仮定と検証状態 | 判断の仮定と、実測・模擬評価・未検証の区別 |

タクソノミーの経路にはサイトマップがないため、サイトマップを必須にしない。

### 再開の条件

UI/UXが返却されたIA成果物を画面に反映してよいのは、次をすべて確認できた場合だけである。

- `run.yaml` の `status` が `completed` である。
- `findings.yaml` に、`severity: critical` かつ `status: open` または `unresolved` の指摘が残っていない。
- 返す成果物に `stale` のものがなく、返却する版で契約検査に通っている。

実行状態、指摘、成果物の版を確認できない場合も、画面への反映を再開させない。`stale` の成果物は該当段階を再実行するか、影響がないことを確かめて再登録する。`unresolved`・`interrupted` などで終わった実行は、成果物に `stale` がなくても再開条件を満たさない。未解決の重大指摘を解消し、必要な段階と契約検査を再実行して正常完了してから返す。

条件を満たさない返却も、run ID、実行状態、未解決の指摘、成果物の参照と、満たしていない再開条件をUI/UXへ報告する。UI/UX側では依存する課題を `verified` にせず、実行記録の `remaining` と `resume` に残す（`ui-architecture` の「IA 群への委譲」）。`major`・`minor` の残件は、既存の終了規則どおり理由と影響を報告し、重大指摘と区別する。
