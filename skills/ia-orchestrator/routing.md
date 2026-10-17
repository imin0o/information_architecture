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

Web IAとタクソノミーの両方に当てはまる場合は、ページの配置や探索経路を求めているかで決める。求めていればWeb IA、語彙と分類だけならタクソノミーとする。判定に迷う場合は、ユーザーに課題の種類を質問する（質問は他の不足項目と合わせて5項目以内）。

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
