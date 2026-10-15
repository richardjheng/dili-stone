# M0 資料契約（0.1.0）

以 `dili.models` 的 Pydantic v2 模型為唯一來源，Python ≥ 3.11，src layout。所有範例與測試題文均為手寫假資料，沒有教材或考卷原句；`term_blueprint.json` 只採用已授權的題數／配分結構。模型拒絕未知欄位，使用不可變物件與 tuple；修改請用會重新驗證的 `model_copy(update={...})`。

| 模型 | 用途與主要欄位 |
| --- | --- |
| `Lesson` | 穩定 `id`、出版社／冊次／單元／標題／學年度，`completeness=complete/partial` 表示完整／部分對應。`sources` 含檔名、1 起算 PDF 頁序、字串印刷頁碼、可選 URL、使用條件。 |
| `VocabEntry`／`Sense`／`GrammarPoint` | 詞條有多詞性、片語旗標、可選分類標題及來源 ID。`category_heading=None` 僅表示原文沒有分類標題，不可拿來掩蓋漏抽；有標題時須保存。`textbook_category=application/recognition` 是應用／認識；每個詞義獨立保存中文義與 `is_secondary`，不覆蓋教材分類。文法僅存名稱與自行撰寫的說明。詞條、詞義、文法皆有審核紀錄。 |
| `WordList` | 課綱字表屬明列先備，不自動成為考點。`load_wordlist(csv_path, metadata_path=None, wordlist_id=None)` 預設同名 `.meta.yaml` 與檔名 ID；UTF-8／BOM、嚴格 CSV 欄位 `headword,pos,basic_1200,note`、布林 true/false、1/0、yes/no，核對列數及重複詞條／詞性。 |
| `Blueprint`／`Section` | 考試資訊、卷頭欄位、`scope` 課次 ID、明列的 `prerequisite.wordlist_id` 與 `lesson_ids`（可明列空陣列）、總題數／總分。五種題型自由選；閱讀與克漏字以 `group_count`、`items_per_group` 記每組子題數。`part` 將連續 sections 合併顯示為同一大題，詳見下文。題數一律指計分子題，配分以 Decimal 精確加總。 |
| `Item`／`Group` | 題號、section_id、題型、`polarity`、題幹、A–D 選項或書寫提示、答案、中文應選／不應選理由、`targets` 詞彙／文法 ID、`glossed` 生字中文註解。題組共用 passage／Figure（可同時有）；克漏字以 `{{26}}` 等對應有序連續子題，整卷題號由藍圖節次決定。 |
| `Figure` | 依 `kind` 的 union：`street_map`、`route`、`table`、`venn`。地圖採 StreetMap v2 街廓／面向／格位制，起點朝向與絕對方位步驟分開。路線依站序，時間以服務日起算分鐘，跨日可超過 1440；表格保存有 ID 的欄列與字串儲存格／單位；Venn 的 left/right 是完整集合，intersection 必須等於交集。 |
| `Batch` | 一個 run_id／section_id／produced_by，包含單題或題組，拒絕混放。`validate_against(blueprint, lessons=...)` 驗證節次、題型、數量、題號、圖片權限，提供教材目錄時才檢查考點 ID 確實屬於 scope；不會把先備或生字視為考點。 |

**審核規則：** `reviews` 保存 reviewer、human/ai、含時區時間、content_hash、decision、notes。`add_review(...)` 回傳新物件；人工 approve 才可核准，AI 只允許 note。`status=pending/reviewed`（待審／已審）與 SHA-256 `content_hash` 都由內容計算，匯入的同名值不作核准依據。雜湊包含所有內容及 `unresolved`，排除所有層級的審核紀錄與衍生狀態。內容變更使舊核准失效、保留歷史；附註不改內容 hash。同內容最後一筆人工決定為準。詞義、先備、文法未確認時放入 `unresolved`；即使有核准仍為待審。父模型包含待審子模型時也維持待審，題組共用內容變動會使題組待審。這是資料契約，不是人員身分認證；真正人工操作與紀錄可信度由上層流程保障，M4 不可接受生成者自稱 human 的核准。

**字表 metadata：** 只接受標準鍵。必填 `source_url, version, retrieved_date, usage_terms, entry_count`；可另記 `source_document, basic_1200_count, method, known_issues`（最後一項為文字清單），提供 1200 筆數時也核對實際旗標。舊中文鍵及 `license`、`count`、`source_title` 等別名一律拒絕。日期採 ISO YYYY-MM-DD。唯一鍵為大小寫敏感的 `(headword, pos)`，因此 `Miss`／`miss` 可以共存，完全相同的詞條與詞性仍拒絕；loader 會去掉欄位首尾空白，不改大小寫。來源詞性保持原字串，空字串表示未知，不猜詞義。整合測試會讀取既有 `data/wordlists/moe2000.csv` 與其 metadata，驗證共 2005 筆、`basic_1200=True` 共 1211 筆。這只驗證本地檔案與 loader 契約，不等同重新核對官方 PDF；載入不自動人工核准。

## 大題與題目極性

`Section.part: Text | None = None` 是大題共用標題。同名 part 必須在 sections 中連續，經過其他 part 或 `None` 後再出現會拒絕。`None` 的 section 各自成一大題，即使 title 相同也不合併。section ID、題型、題組大小與配分仍獨立保存，Batch 繼續依 section 驗證。

`Blueprint.parts()` 按原順序回傳不可變的 `tuple[PartSummary, ...]`；每項有 `title`（part 或單獨 section 的 title）、`sections`、衍生的 `total_items`、`total_points`、`points_per_item`。題數／總分由 sections 加總；各 section 每題配分一致時才回傳單一 Decimal，否則 `points_per_item=None`，M3 不應顯示單一「每題幾分」。範例的克漏字 6 題與閱讀 14 題共用「第二部分、題組」，合計 20 題、30 分、每題 1.5 分。

`Item.polarity` 可為 `best`（預設，選最佳答案）、`wrong`（選錯誤敘述）、`correct`（選正確敘述）。只有 `single_choice`、`cloze`、`reading` 可以使用非預設值；`word_in_context` 與 `guided_writing` 限 `best`。`answer.choice` 始終是**應選的選項**，不隨 polarity 翻轉，也不代表該選項敘述必定正確。

解析欄位名保持相容：`explanation.correct_zh` 說明為何應選該答案（書寫題則說明可接受答案）；`explanation.distractors` 必須恰好解釋另外三個選項為何不應選，`reason_zh` 不必指稱敘述本身錯誤。選錯題中，未選項可能正是正確敘述，見 `examples/synthetic_wrong_item.json`。模型檢查欄位與選項覆蓋，不判斷自然語言真假；改 polarity 也會改內容 hash，使舊核准失效。

## StreetMap v2

原點在西南，x 向東、y 向北。每個整數 x 是南北向街（`vertical_streets[x]`），每個整數 y 是東西向街（`horizontal_streets[y]`）；width/height 為街廓數，街名清單長度分別須為 width+1／height+1。`start` 為交叉口，可取邊界 x=width、y=height；`Place.block={x: bx, y: by}` 是街廓左下角，嚴格要求 `0 <= bx < width`、`0 <= by < height`。

`Place` 欄位為 `id, name, kind, block, side, slot`。`kind` 必填 `building` 或 `stop`，`slots_per_side=k` 為全圖共用的嚴格整數且 k≥2，slot 為 0..k−1 的嚴格整數。

| side | 面向的街 | slot 增加方向 | slot 0／k−1 |
| --- | --- | --- | --- |
| N | horizontal_streets[by+1] | 西→東 | 西北角／東北角 |
| S | horizontal_streets[by] | 西→東 | 西南角／東南角 |
| E | vertical_streets[bx+1] | 南→北 | 東南角／東北角 |
| W | vertical_streets[bx] | 南→北 | 西南角／西北角 |

同街廓 building 實際位置須唯一：不只拒絕同 side／slot，也拒絕 N/0 與 W/k−1 等跨邊重複佔角。同一建物只保存一個面向。不同街廓的轉角各自獨立，允許街道對面各有建物。stop 可與 building 同格位；但跨邊共角且面向不同不算 `in_front_of`。舊 `Place.position` 整數交叉口格式已移除，不自動猜測轉換。

以下為 `StreetMap` 的純查詢方法，不修改模型；參數可用本圖的 Place 或 place ID。未知 ID 或與本圖資料不符的 Place 會 raise ValueError。

| 查詢 | 精確定義 |
| --- | --- |
| `on_street(a)` | 依 side 回傳唯一面向的街名；轉角也只回傳面向街。 |
| `corner_of(a)` | slot=0 或 k−1 回傳 `(東西向街名, 南北向街名)`，否則 None。 |
| `next_to(a,b)` | 兩者都是 building、同 block、同 side、slot 差的絕對值為 1。空格不略過，不跨轉角。 |
| `between(a,b,c)` | 三者都是 building，a 與 b、c 同 block 同 side，且 b、a、c 為相鄰的三個格位；交換 b、c 不影響結果。 |
| `across_from(a,b)` | 面向同一條街的相反兩側、位於相鄰街廓，沿街座標相同；N/S 比 bx 與 slot，E/W 比 by 與 slot。可查 building 或 stop，依街道座標判斷，不只比街名字串。 |
| `in_front_of(stop,building)` | 第一參數為 stop、第二為 building，block／side／slot 三者完全一致；參數方向有意義。 |

問路使用 `start`、`facing` 與 `directions: tuple[MapStep, ...]`；MapStep 的 direction 為 north/east/south/west **絕對方向**，blocks 為正整數，不以 facing 轉換。每段都須留在街道網格中。`endpoint()` 回傳最後交叉口，無步驟時回傳 start；此結果不是地點。

可選 `arrival={"side":"left"|"right", "position":"corner"|正整數}`；例如 position=2 表示由終點往回數第二棟 building。有 arrival 時必須至少有一段 MapStep；`destination()` 回傳唯一 Place，模型建立時也會驗證抵達條件：

- 左右以最後一段實際行進方向決定，與起始 facing 無關。
- 只看終點**之前最後一個街廓**、指定左右側、且面向行經街道的 building。多街廓的步驟也不把較早街廓納入。
- 正整數序數由終點向後排序數 building，略過空格與 stop；不因該側有多棟建物就拒絕。
- corner 必須是終點角落的 building，且面向行經街道；不可退回最近一棟，只有 stop 也不算。
- 沒有符合的建物（含序數超出、指定地圖外側）或結果不唯一時拒絕。無 arrival 時呼叫 `destination()` 也會拒絕。

`examples/figures.json` 示範走到 (2,1) 後查詢左側轉角 Art Studio，並有同格位的 Studio Stop。這套規則只涵蓋明定的問路形式，題幹應表明序數由終點回數；不推論所有自然語言路線題。

**使用與驗證：** 安裝後由專案根目錄執行 `python -m dili.models.export_schema`，輸出八類 `schemas/<name>.json`（作者輸入）及 `<name>.output.json`（正規化輸出，含唯讀狀態／hash），共 16 檔。作者 JSON 交給 `Model.model_validate`；正規化 JSON 可再用 `model_validate_json` 載回。JSON Schema 表達欄位與 union，跨欄位總分、引用、審核等仍須執行 Pydantic 驗證。測試使用 unittest 寫法，也可由 pytest 收集；現有 `agora check` 會跑 unittest，並由匯出整合測試更新 `schemas/`。

**後續界線：** 地圖關係與抵達查詢、Route 的 `destination(start_id, stops_ahead)`／`travel_minutes()`、`cell()`、`members()` 提供圖片答案驗算的資料查詢；實際繪圖及將自然語言題意連到查詢由 M2/M3 處理。中文欄位只檢查含中文字，不保證解析正確；字詞超範、歧義、先備詞義確認與實際人工審題屬後續流程。範例將 20 題題組暫分為克漏字 6 題／閱讀 14 題，僅示範可變組大小，實際出卷配置仍待確認。M0b 依討論共識修訂上述契約，模型版號暫維持 0.1.0。
