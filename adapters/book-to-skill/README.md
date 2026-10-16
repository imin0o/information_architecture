# book-to-skill アダプター（BR-01）

上流の抽出規則と、本プロジェクトの固定抽出スナップショットとの対応を記録する。実装は `scripts/iakit/extract_pdf.py`、`extract_epub.py`、`snapshot.py` にある。CLIは `iakit extract run` と `iakit extract check`。

## 上流の調査対象

| 項目 | 値 |
|---|---|
| リポジトリ | https://github.com/virgiliojr94/book-to-skill |
| コミット | `a24e62f4d8caf2ee433f6e48d9073800f56f906e`（package version 1.4.0） |
| ライセンス | MIT |
| 実行 | 上流コードは依存に加えず、実行もしない（`extraction.yaml` の `upstream.code_executed: false`） |

上流のgit依存の追加は、作業環境の権限設定で外部コードの取り込みとして拒否された。上流のコードを実行するかどうかは保留事項 P-M1-01 とし、現状は同じ規則を本プロジェクト側で実装している。

## 上流と同じにした点、変えた点

| 形式 | 上流の処理 | アダプターの処理 | 理由 |
|---|---|---|---|
| PDF | pdftotext、pdf-inspector、pypdf、pdfminerの順に試し、ページを連結して整形する | pypdf 5.9.0 の `page.extract_text()` をページごとに呼ぶ。フォールバックはない | 抽出方式を1つに固定し、どの方式で抽出したかを記録と一致させる。Windowsの検証環境には pdftotext がない |
| PDF | `clean_pdftotext` がヘッダー・フッター・ページ番号の行を除き、行末ハイフンを結合する | 整形しない（`postprocess: none`） | 整形でページ境界が失われ、本文行も削除・結合されうる。原典照合では書かれたままの行を使う |
| PDF | 印刷ページ番号は扱わない | PageLabels があるときだけ `printed_page_label` に記録する。ないときは null | PageLabels がないPDFでpypdfが返す番号はページ順であり、印刷番号ではない |
| EPUB | stdlib抽出器でscript・style・headを除き、ブロック要素で改行、表のセルをタブで区切る | 同じ規則を実装し、要素IDの位置と画像等の読めない要素を記録する | 上流の出力はspine項目の境界と要素IDを持たない |
| EPUB | 本文前の空白も出力する | 文書の本文が始まる前の空白だけのデータを捨てる | XML宣言やbody直後の改行が、各文書の先頭に空行として入るため |
| 共通 | `sanitize_extracted_text` が不可視文字を除き、康熙部首を統合漢字へ畳み込む | 変換しない。不可視文字の件数だけ `quality.invisible_codepoints` に記録する | BR-01はUnicodeを追加変換しない。資料由来の隠れた命令はM3の知識統合で扱う |

取り込んでいない上流の機能: CLI、Skill生成・インストール・公開、一時データの削除、LLMを使う手順。抽出処理ではLLMを呼ばない（`llm_calls: 0`）。

## スナップショットの契約

`sources/extracted/<extraction_id>/`（Git管理外）に次の3ファイルを保存する。

- `text.txt`: UTF-8、BOMなし、改行LF、Unicodeは変換しない。ページ・文書ごとに末尾をLFにそろえ、区切り行を入れずに連結する。
- `locations.yaml`: 抽出ID・テキストのハッシュと、ページ・spine文書ごとの行範囲（1始まり、両端を含む）。PDFは印刷ページ番号と画像数、EPUBはhref・要素IDの行・読めない要素を持つ。
- `extraction.yaml`: 原典ハッシュ、抽出方式と版、正規化、テキストと対応表のハッシュ、品質の件数。`bibliography_state` は抽出時点の書誌状態で、`trial` は書誌未確認の試験抽出を表す。

IDは `EXTRACT-<書籍番号>-<連番>`。再抽出には新しい連番を割り当て、既存のディレクトリは上書きしない。作業中は `.EXTRACT-…partial` に書き、完成後に名前を変える。

抜粋のハッシュは付録B.1に従い、指定範囲の各行を末尾LFごと取り出したバイト列から計算する。`iakit excerpt ref` はハッシュと位置の候補だけを出力し、本文は出さない。位置の候補は機械的な対応であり、原典で人が確認するまで `provenance.yaml` の locator に確定させない。
