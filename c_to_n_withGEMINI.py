# ===============================================================
# Caption to Narration  ver1.00（整理版）
# 目的：ロジックは完全据え置き／途中説明の削除／ブロック見出しのみ付与
# ===============================================================

# -----------------------------
# [Imports]
# -----------------------------
import streamlit as st
import re
import math
import hashlib  # ← 追加
from google import genai
from google.genai.errors import APIError


# -----------------------------
# [AI校正] Gemini 呼び出し
# -----------------------------
def check_narration_with_gemini(narration_blocks, api_key):
    """Gemini APIを使用してナレーションの誤字脱字をチェックする"""
    if not api_key:
        return "エラー：Gemini APIキーが設定されていません。Streamlit Secretsを確認してください。"

    try:
        client = genai.Client(api_key=api_key)

        formatted_text = "\n".join([f"[{b['time']}] {b['text']}" for b in narration_blocks])

        prompt = f"""
        あなたはプロフェッショナルな校正者です。
        以下のナレーション原稿のリストを、テレビでおかしくないように誤字脱字、不適切な表現、文法ミスがないか厳密にチェックしてください。
        
        【指示】
        1. 入力された本文（「本文」カラムの内容）は**一切変更しないで**ください。
        2. 誤りが見つかった場合のみ、以下の Markdown テーブル形式で修正提案とその理由を出力してください。
        3. 誤りがない場合は、「問題ありませんでした。」とだけ出力してください。
        4. 芸能人の名前の漢字間違いも指摘してください
        5. テレビは子どもも見るので難しすぎる漢字は避けたいです
        
        【出力形式】
        | 原文の位置 | 本文 | 修正提案 | 理由 |
        |---|---|---|---|
        | (行番号または特定箇所) | (誤っている単語・フレーズ) | (正しい単語・フレーズ) | (修正理由) |
        
        【ナレーション原稿】
        ---
        {formatted_text}
        ---
        """

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )

        return response.text

    except APIError as e:
        return f"Gemini APIエラーが発生しました。詳細: {e}"
    except Exception as e:
        return f"予期せぬエラー: {e}"
# === AI結果のMarkdown表を安全にパース（本文・修正提案・理由） ===
def _parse_ai_markdown_table(md_text: str):
    """
    期待形式:
      | 原文の位置 | 本文 | 修正提案 | 理由 |
      |---|---|---|---|
      | ... | ... | ... | ... |
    または自由記述:
      例) 「出川哲郎」の正しくは「出川哲朗」
          出川哲郎 → 出川哲朗  のような表記ゆれ
    戻り値: [{'body': '本文(誤記側)', 'suggestion': '修正提案', 'reason': '理由'}...]
    """
    if not md_text or "問題ありませんでした" in md_text:
        return []

    rows = []

    # --- まずはMarkdown表を厳密にパース ---
    lines = [ln.strip() for ln in md_text.splitlines()]
    for line in lines:
        if not (line.startswith("|") and line.endswith("|")):
            continue
        # ヘッダ罫線はスキップ
        core = line.replace("|", "").strip()
        if set(core.replace(" ", "")) <= {"-", ":"}:
            continue

        parts = [p.strip() for p in line.split("|")[1:-1]]
        if len(parts) >= 4:
            body = parts[1]
            suggestion = parts[2]
            reason = parts[3]
            if body or suggestion or reason:
                rows.append({"body": body, "suggestion": suggestion, "reason": reason})

    # --- 表で1件も取れなければ自由記述をゆるく抽出 ---
    if not rows:
        import re
        text = md_text.replace("「", "").replace("」", "").replace("『", "").replace("』", "")
        # パターン1: A の正しくは B
        for m in re.finditer(r"(.+?)の?正しくは(.+?)(。|$|\n)", text):
            body = m.group(1).strip()
            suggestion = m.group(2).strip()
            rows.append({"body": body, "suggestion": suggestion, "reason": ""})
        # パターン2: A → B
        for m in re.finditer(r"(.+?)\s*→\s*(.+?)(。|$|\n)", text):
            body = m.group(1).strip()
            suggestion = m.group(2).strip()
            rows.append({"body": body, "suggestion": suggestion, "reason": ""})

    return rows



# === 変換済みテキストの各行の“該当箇所の直下”に※注記を追記（本文自体は不変） ===
def _annotate_narration_with_ai_notes(converted_text: str, findings: list, max_note_len: int = 15) -> str:
    """
    converted_text: 既存のナレーション出力（本文は改変しない）
    findings: [{'body':..., 'suggestion':..., 'reason':...}]
    仕様:
      - 本文行に 'body' が含まれていれば、その直下に ※注記行を1回だけ挿入
      - マッチは全角/半角・空白ゆれを正規化して行う
      - 注記は 15 文字に丸める
    """
    if not findings:
        return converted_text

    # --- 全角化・空白除去などの正規化（本文出力に合わせる） ---
    def _to_zenkaku(s: str) -> str:
        if not s:
            return ""
        # 英数と基本記号を全角化
        hankaku = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !@#$%&-+='
        zenkaku = 'ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９　！＠＃＄％＆－＋＝'
        tbl = str.maketrans(hankaku, zenkaku)
        # 数字は念押しで全角化（上のテーブルにも含むが二重でも無害）
        num_tbl = str.maketrans('0123456789', '０１２３４５６７８９')
        return s.translate(tbl).translate(num_tbl)

    def _norm_for_match(s: str) -> str:
        s = _to_zenkaku(s)
        # 空白・全角空白を除去して比較強化
        s = s.replace(" ", "").replace("　", "")
        return s

    lines = converted_text.split("\n")
    annotated = []
    used = set()
    INDENT = "　" * 9  # 全角インデント

    for line in lines:
        annotated.append(line)

        line_norm = _norm_for_match(line)

        for idx, f in enumerate(findings):
            if idx in used:
                continue

            body = (f.get("body") or "").strip()
            sug  = (f.get("suggestion") or "").strip()
            reason = (f.get("reason") or "").strip()

            # body（誤記側）が空のこともあるので、suggestionも候補に
            candidates = [c for c in [body, sug] if c]

            matched = False
            for c in candidates:
                if _norm_for_match(c) and _norm_for_match(c) in line_norm:
                    matched = True
                    break

            if not matched:
                continue

            # 注記本文
            if sug:
                core = f"正しくは{sug}では？"
            elif reason:
                core = reason
            else:
                core = "要確認"

            if len(core) > max_note_len:
                core = core[:max_note_len] + "…"

            note = f"{INDENT}※{core}"
            annotated.append(note)
            used.add(idx)

    return "\n".join(annotated)


# ===============================================================
# ▼▼▼ AI結果の整形ユーティリティ（追記：原文改変なしで下行に注記を入れる） ▼▼▼
# ===============================================================
def _parse_ai_markdown_table(md_text: str):
    """
    Geminiの出力（Markdown表 or '問題ありませんでした。'）を解析して
    [{'pos': '...', 'orig': '...', 'suggest': '...', 'reason': '...'}, ...] を返す
    """
    findings = []
    if not md_text or "問題ありませんでした" in md_text:
        return findings

    lines = [ln.strip() for ln in md_text.splitlines()]
    rows = []
    for ln in lines:
        if ln.startswith("|") and ln.endswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            rows.append(cells)

    # ヘッダ行・区切り行を除いた実データ行のみ抽出
    data_rows = []
    for cells in rows:
        if len(cells) < 4:
            continue
        # 区切り行（---）を除外
        if all(set(c) <= {"-", ":"} for c in cells):
            continue
        data_rows.append(cells)

    # 先頭がヘッダなら落とす
    if data_rows and ("原文の位置" in data_rows[0][0] or "本文" in data_rows[0][1]):
        data_rows = data_rows[1:]

    for cells in data_rows:
        try:
            pos, orig, suggest, reason = cells[0], cells[1], cells[2], cells[3]
        except Exception:
            continue
        findings.append({
            "pos": pos,
            "orig": orig,
            "suggest": suggest,
            "reason": reason
        })
    return findings


def _annotate_narration_with_ai_notes(narration_text: str, findings):
    """
    右側テキストエリアに表示するため、
    原文行はそのまま・該当行の直下に 「※正しくは〇〇では？」 を追記する。
    - 原文の『本文』をその行に含むかどうかでマッチング（曖昧一致）。
    - 同一行に複数指摘があれば、指摘行を複数挿入。
    - 追記は表示上の加工のみ。元変換ロジックの出力は改変しない。
    """
    if not findings:
        return narration_text

    lines = narration_text.splitlines()
    new_lines = []
    for line in lines:
        new_lines.append(line)

        # 本文候補（行全体から探す簡易一致）
        # NやVO等の記号まで厳密に分解せず、まずは「本文」文字列が含まれるかで判定
        matches_for_this_line = []
        for f in findings:
            orig = (f.get("orig") or "").strip()
            suggest = (f.get("suggest") or "").strip()
            if not orig or not suggest:
                continue
            if orig in line:
                matches_for_this_line.append(f)

        # マッチがあれば、行の直下に注記を追記
        for f in matches_for_this_line:
            suggest = f["suggest"]
            # できるだけ短く・疑問形で
            note = f"　　　　　　　　　※正しくは{suggest}では？"
            new_lines.append(note)

    return "\n".join(new_lines)


# -----------------------------
# [AI] 指摘パース & プレビュー生成（本文は絶対に改変しない）
# -----------------------------
import csv
import io
import textwrap
import re

def _parse_ai_markdown_table(ai_md: str):
    """
    GeminiのMarkdown表（| 原文の位置 | 本文 | 修正提案 | 理由 |）をパースして
    [{'pos':..., 'orig':..., 'proposal':..., 'reason':...}, ...] を返す。
    表が無い/「問題ありませんでした。」の場合は空配列。
    """
    if not ai_md or "問題ありませんでした" in ai_md:
        return []

    # Markdown表部分だけ抽出
    lines = [ln.strip() for ln in ai_md.strip().splitlines() if ln.strip()]
    table_lines = [ln for ln in lines if ln.startswith("|") and ln.endswith("|")]
    # 見出しと区切り（|---|）を除いた実データ行
    data_lines = []
    header_seen = False
    for ln in table_lines:
        if re.match(r"^\|\s*---", ln):
            header_seen = True
            continue
        if header_seen:
            data_lines.append(ln)

    records = []
    for row in data_lines:
        # 先頭末尾の|を落としてセル分割
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 4:
            continue
        pos, orig, proposal, reason = cells[:4]
        records.append({
            "pos": pos,
            "orig": orig,
            "proposal": proposal,
            "reason": reason,
        })
    return records

def _shorten_note(text: str, limit: int = 15) -> str:
    """全角/半角混在でも単純に文字数で詰める（要件：15文字以内）"""
    if text is None:
        return ""
    t = text.strip()
    return t if len(t) <= limit else t[:limit]

def build_annotated_preview(narration_text: str, ai_md: str) -> str:
    """
    オリジナルのナレーション本文を一切変えず、
    “各対象行の直下に”『※短い指摘（15文字以内）』行を足した「プレビュー」を生成。
    マッチングは「本文セル（orig）」の部分一致で最初に見つかった行に付与。
    """
    findings = _parse_ai_markdown_table(ai_md)
    if not findings:
        # 指摘無し：そのまま返す
        return narration_text

    lines = narration_text.splitlines()
    out = []
    used = set()  # 同一行に多重付与し過ぎないための軽い制御

    for idx, line in enumerate(lines):
        out.append(line)
        # その行に当てはまる指摘を拾う（複数あっても控えめに2件まで）
        matched = []
        for f in findings:
            key = f["orig"]
            if key and key in line:
                matched.append(f)
        if matched:
            # 2件まで、提案優先・なければ理由
            count = 0
            for f in matched:
                note_src = f["proposal"].strip() or f["reason"].strip()
                note = _shorten_note(note_src, 15)
                if note:
                    out.append(f"※{note}")
                    count += 1
                    if count >= 2:
                        break
    return "\n".join(out)


# -----------------------------
# [変換エンジン] Caption → Narration
# -----------------------------
def convert_narration_script(text, n_force_insert_flag=True, mm_ss_colon_flag=False):
    FRAME_RATE = 30.0
    CONNECTION_THRESHOLD = 1.0 + (10.0 / FRAME_RATE)

    to_zenkaku_num = str.maketrans('0123456789', '０１２３４５６７８９')

    hankaku_symbols = '!@#$%&-+='
    zenkaku_symbols = '！＠＃＄％＆－＋＝'
    hankaku_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ' + hankaku_symbols
    zenkaku_chars = 'ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９　' + zenkaku_symbols
    to_zenkaku_all = str.maketrans(hankaku_chars, zenkaku_chars)

    to_hankaku_time = str.maketrans('０１２３４５６７８９：〜', '0123456789:~')

    lines = text.strip().split('\n')
    start_index = -1
    time_pattern = r'(\d{2})[:;](\d{2})[:;](\d{2})[;.](\d{2})\s*-\s*(\d{2})[:;](\d{2})[:;](\d{2})[;.](\d{2})'

    for i, line in enumerate(lines):
        line_with_frames = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', line)
        normalized_line = line_with_frames.strip().translate(to_hankaku_time).replace('~', '-')
        if re.match(time_pattern, normalized_line):
            start_index = i
            break

    if start_index == -1:
        return "エラー：変換可能なタイムコード（フレーム情報を含む形式）が見つかりませんでした。"

    relevant_lines = lines[start_index:]

    blocks = []
    i = 0
    while i < len(relevant_lines):
        current_line = relevant_lines[i].strip()
        line_with_frames = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', current_line)
        normalized_line = line_with_frames.translate(to_hankaku_time).replace('~', '-')

        if re.match(time_pattern, normalized_line):
            time_val = current_line
            text_val = ""
            if i + 1 < len(relevant_lines):
                next_line = relevant_lines[i+1].strip()
                next_normalized_line = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', next_line).translate(to_hankaku_time).replace('~', '-')
                if not re.match(time_pattern, next_normalized_line):
                    text_val = next_line
                    i += 1
            blocks.append({'time': time_val, 'text': text_val})
        i += 1

    output_lines = []

    narration_blocks_for_ai = []
    parsed_blocks = []
    for block in blocks:
        line_with_frames = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', block['time'])
        normalized_time_str = line_with_frames.translate(to_hankaku_time).replace('~', '-')
        time_match = re.match(time_pattern, normalized_time_str)
        if not time_match:
            continue

        groups = time_match.groups()
        start_hh, start_mm, start_ss, start_fr, end_hh, end_mm, end_ss, end_fr = [int(g or 0) for g in groups]

        narration_blocks_for_ai.append({
            'time': block['time'].strip(),
            'text': block['text'].strip()
        })

        parsed_blocks.append({
            'start_hh': start_hh, 'start_mm': start_mm, 'start_ss': start_ss, 'start_fr': start_fr,
            'end_hh': end_hh, 'end_mm': end_mm, 'end_ss': end_ss, 'end_fr': end_fr,
            'text': block['text']
        })

    for i, block in enumerate(parsed_blocks):
        start_hh, start_mm, start_ss, start_fr = block['start_hh'], block['start_mm'], block['start_ss'], block['start_fr']
        end_hh, end_mm, end_ss, end_fr = block['end_hh'], block['end_mm'], block['end_ss'], block['end_fr']

        should_insert_h_marker = False
        marker_hh_to_display = -1

        if i == 0:
            if start_hh > 0:
                should_insert_h_marker = True
                marker_hh_to_display = start_hh
            previous_end_hh = end_hh
        else:
            if start_hh < end_hh:
                should_insert_h_marker = True
                marker_hh_to_display = end_hh
            elif start_hh > previous_end_hh:
                should_insert_h_marker = True
                marker_hh_to_display = start_hh

        if should_insert_h_marker:
            output_lines.append("")
            output_lines.append(f"【{str(marker_hh_to_display).translate(to_zenkaku_num)}Ｈ】")
           # output_lines.append("")

        previous_end_hh = end_hh

        total_seconds_in_minute_loop = (start_mm % 60) * 60 + start_ss
        spacer = ""

        is_half_time = False
        base_time_str = ""

        if 0 <= start_fr <= 9:
            display_mm = (total_seconds_in_minute_loop // 60) % 60
            display_ss = total_seconds_in_minute_loop % 60
            base_time_str = f"{display_mm:02d}{display_ss:02d}"
            spacer = "　　　"
        elif 10 <= start_fr <= 22:
            display_mm = (total_seconds_in_minute_loop // 60) % 60
            display_ss = total_seconds_in_minute_loop % 60
            base_time_str = f"{display_mm:02d}{display_ss:02d}"
            spacer = "　　"
            is_half_time = True
        else:
            total_seconds_in_minute_loop += 1
            display_mm = (total_seconds_in_minute_loop // 60) % 60
            display_ss = total_seconds_in_minute_loop % 60
            base_time_str = f"{display_mm:02d}{display_ss:02d}"
            spacer = "　　　"

        if mm_ss_colon_flag:
            mm_part = base_time_str[:2]
            ss_part = base_time_str[2:]
            colon_time_str = f"{mm_part}：{ss_part}"
        else:
            colon_time_str = base_time_str

        if is_half_time:
            formatted_start_time = f"{colon_time_str.translate(to_zenkaku_num)}半"
        else:
            formatted_start_time = colon_time_str.translate(to_zenkaku_num)

        speaker_symbol = 'Ｎ'
        text_content = block['text']
        body = ""

        if n_force_insert_flag:
            match = re.match(r'^(\S+)\s+(.*)', text_content)
            if match:
                raw_speaker = match.group(1)
                body = match.group(2).strip()
                if raw_speaker.upper() == 'N':
                    speaker_symbol = 'Ｎ'
                else:
                    speaker_symbol = raw_speaker.translate(to_zenkaku_all)
            else:
                if text_content.upper() == 'N' or text_content == 'Ｎ':
                    body = ""
                elif text_content.startswith('Ｎ '):
                    body = text_content[2:].strip()
                elif text_content.startswith('N '):
                    body = text_content[2:].strip()
                else:
                    body = text_content
            if not body:
                body = "※注意！本文なし！"
        else:
            speaker_symbol = ''
            body = text_content
            if not body.strip():
                body = "※注意！本文なし！"

        body = body.translate(to_zenkaku_all)

        end_string = ""
        add_blank_line = True

        if i + 1 < len(parsed_blocks):
            next_block = parsed_blocks[i+1]
            end_total_seconds = (end_hh * 3600) + (end_mm * 60) + end_ss + (end_fr / FRAME_RATE)
            next_start_total_seconds = (next_block['start_hh'] * 3600) + (next_block['start_mm'] * 60) + next_block['start_ss'] + (next_block['start_fr'] / FRAME_RATE)
            if next_start_total_seconds - end_total_seconds < CONNECTION_THRESHOLD:
                add_blank_line = False

        if add_blank_line:
            adj_ss = end_ss
            adj_mm = end_mm
            if 0 <= end_fr <= 9:
                adj_ss = end_ss - 1
            if adj_ss < 0:
                adj_ss = 59
                adj_mm -= 1

            adj_mm_display = adj_mm % 60

            if start_hh != end_hh or (start_mm % 60) != adj_mm_display:
                formatted_end_time = f"{adj_mm_display:02d}{adj_ss:02d}".translate(to_zenkaku_num)
            else:
                formatted_end_time = f"{adj_ss:02d}".translate(to_zenkaku_num)

            end_string = f" (～{formatted_end_time})"

        if n_force_insert_flag:
            output_lines.append(f"{formatted_start_time}{spacer}{speaker_symbol}　{body}{end_string}")
        else:
            output_lines.append(f"{formatted_start_time}{spacer}{body}{end_string}")

        if add_blank_line and i < len(parsed_blocks) - 1:
            output_lines.append("")

    return {"narration_script": "\n".join(output_lines), "ai_data": narration_blocks_for_ai}


# -----------------------------
# [UI] ページ設定・ヘッダ
# -----------------------------
st.set_page_config(page_title="Caption to Narration", page_icon="📝", layout="wide")
st.title('Caption to Narration')

GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

st.markdown("""<style> 
textarea::placeholder { font-size: 13px; } 
textarea { font-size: 14px !important; }
</style>""", unsafe_allow_html=True)

col1, col2 = st.columns(2)

help_text = """
  
【機能詳細】  
・ENDタイム(秒のみ)が自動で入ります  
　分をまたぐ時は(分秒)、次のナレーションと繋がる時は割愛されます  
・Hをまたぐときは自動で仕切りが入ります  
   
・✅N強制挿入がONの場合、自動で全角Ｎが挿入されます  
　　※ＶＯや実況などはそのまま表記  
・ナレーション本文の半角英数字は全て全角に変換します  
・✅ｍｍ：ｓｓで出力がONの場合タイムに：が入ります  
・✅誤字脱字チェックをONにするとgeminiが頑張ります  
　　※精度低いのでテスト機能です
"""


# -----------------------------
# [UI] 1段目：入力／出力エリアの枠
# -----------------------------
col1_top, col2_top = st.columns(2)
with col1_top:
    st.header('')
with col2_top:
    st.header('')

col1_main, col2_main = st.columns(2)
input_text = ""

with col1_main:
    input_text = st.text_area(
        "　ここに元原稿をペースト", 
        height=500, 
        placeholder="""①キャプションをテキストで書き出した形式
00;00;00;00 - 00;00;02;29
N ああああ

②xmlをサイトで変換した形式
００:００:１５　〜　００:００：１８
N ああああ

この２つの形式に対応しています。ペーストして　Ctrl+Enter　を押して下さい
①の方が細かい変換をするのでオススメです

""", 
        help=help_text
    )


# -----------------------------
# [UI] 2段目：オプション列（左寄せ＋右に余白）
# -----------------------------
col1_bottom_opt, col2_bottom_opt, col3_bottom_opt, col4_bottom_spacer = st.columns([1.5, 2, 2, 9])

with col1_bottom_opt:
    n_force_insert = st.checkbox("N強制挿入", value=True)

with col2_bottom_opt:
    mm_ss_colon = st.checkbox("ｍｍ：ｓｓで出力", value=False)

with col3_bottom_opt:
    ai_check_flag = st.checkbox("誤字脱字チェックβ", value=False)


# -----------------------------
# [UI] 3段目：変換実行・結果表示・AI校正表示
# -----------------------------
if input_text:
    #try:
        conversion_result = convert_narration_script(input_text, n_force_insert, mm_ss_colon)
        converted_text = conversion_result["narration_script"]
        ai_data = conversion_result["ai_data"]
        
        # output_text_area を col2_main の中で呼び出す
        # output_text_area を col2_main の中で呼び出す（原文は絶対に改変しない）
        with col2_main:
            # 入力テキストの内容でキャッシュキーを決定（内容が変わったときだけ再呼び出し）
            input_hash = hashlib.md5(input_text.encode("utf-8")).hexdigest()
            if "ai_cache_key" not in st.session_state:
                st.session_state["ai_cache_key"] = None
            if "ai_result_cache" not in st.session_state:
                st.session_state["ai_result_cache"] = ""

            if ai_check_flag:
                # 入力内容が変わっていればAPI再実行、同じならキャッシュ再利用
                if st.session_state["ai_cache_key"] != input_hash:
                    with st.spinner("Geminiが誤字脱字をチェック中..."):
                        ai_result_text = check_narration_with_gemini(ai_data, GEMINI_API_KEY)
# --- ここからデバッグ可視化（表示のみ／ロジック不変更）-----------------
# 1) APIキーの有効性（伏字）とレスポンスの基本情報
def _mask_key(k: str) -> str:
    if not k: return "(未設定)"
    return f"(設定済) head:{k[:3]}***tail:{k[-3:]}"

st.info(
    "Gemini呼び出しデバッグ\n"
    f"- APIキー: {_mask_key(GEMINI_API_KEY)}\n"
    f"- 受信テキスト長: {0 if ai_result_text is None else len(ai_result_text)}",
    icon="🔎"
)

# 2) 生レスポンスをそのまま確認（Markdown扱いだと崩れるので code 表示）
with st.expander("デバッグ: Gemini 生レスポンス（そのまま）", expanded=False):
    st.code(ai_result_text if ai_result_text else "<空>", language="markdown")

# 3) 代表的な異常パターンを警告
if not ai_result_text or "エラー" in ai_result_text or "API" in ai_result_text:
    st.warning("AI応答が空 or エラー文を含んでいます。上の『生レスポンス』を確認してください。", icon="⚠️")
# --- ここまでデバッグ可視化 ----------------------------------------------


                    st.session_state["ai_result_cache"] = ai_result_text or ""
                    st.session_state["ai_cache_key"] = input_hash

                # キャッシュを元に注記行を付与（本文はそのまま）
                findings = _parse_ai_markdown_table(st.session_state["ai_result_cache"])
                display_text = _annotate_narration_with_ai_notes(converted_text, findings)
            else:
                # OFF のときは純粋な変換結果のみを表示（キャッシュは保持するが使わない）
                display_text = converted_text

            st.text_area("　コピーしてお使いください", value=display_text, height=500)

               # )
    except Exception as e:
        with col2_main:
            st.error(f"エラーが発生しました。テキストの形式を確認してください。\n\n詳細: {e}")
            st.text_area("　コピーしてお使いください", value="", height=500, disabled=True)

else:
    with col2_main:
        st.markdown('<div style="height: 500px;"></div>', unsafe_allow_html=True)


# -----------------------------
# [UI] フッター
# -----------------------------
st.markdown("---")
st.markdown(
    """
    <div style="text-align: right; font-size: 12px; color: #C5D6B9;">
        © 2025 kimika Inc. All rights reserved.
    </div>
    """,
    unsafe_allow_html=True
)
