import streamlit as st
import re
import math
import hashlib
from typing import List, Dict, Tuple

# =========================
# Gemini API（高速・軽量化）
# =========================
from google import genai
from google.genai.errors import APIError

# ------------------------------------------------------------
#  ユーティリティ：テキストのハッシュ（AI再実行の抑止用）
# ------------------------------------------------------------
def _digest_blocks(blocks: List[Dict[str, str]]) -> str:
    # タイム＋本文の列挙からMD5を作成（本文が一文字でも変われば別ダイジェスト）
    md5 = hashlib.md5()
    for b in blocks:
        md5.update((b.get("time", "") + "\n" + b.get("text", "") + "\n").encode("utf-8"))
    return md5.hexdigest()

def _ensure_question_15ch(note: str) -> str:
    """先頭に※、末尾は疑問形（「？」）に統一。全体は15文字以内に丸める。"""
    s = (note or "").strip().replace("\n", " ").replace("\r", " ")
    if not s:
        return ""
    # 末尾を疑問形に寄せる
    if not s.endswith("？"):
        s = s.rstrip("。!?？") + "？"
    # 先頭に ※
    s = "※" + s
    # 15文字以内に丸める
    if len(s) > 15:
        s = s[:15]
        # 末尾が中途半端になったら整える（最後が句読点でないならそのままOK）
        # ここではシンプルに切り捨てのみ
    return s

# ------------------------------------------------------------
#  Gemini 呼び出し（行をチャンク分割して高速化・軽量プロンプト）
#  - 半角/全角の差異は無視（今回の変換後は全角化済みなので誤検出抑制）
#  - チャンク内で 0..N の行番号を振り、その番号と短い注意文だけ返させる
#  - 出力仕様: 「index<TAB>note」のみを複数行（OK行は出力なし）
# ------------------------------------------------------------
def check_blocks_with_gemini_fast(
    blocks: List[Dict[str, str]],
    api_key: str,
    model_name: str = "gemini-2.0-flash-lite-preview",
    chunk_size: int = 6,
) -> Dict[int, str]:
    """
    returns: {global_index: short_note_str}
    """
    if not api_key:
        return {}

    try:
        client = genai.Client(api_key=api_key)
    except Exception:
        return {}

    # 念のため、lite が使えない場合は 2.5-flash にフォールバック
    fallback_tried = False
    notes_map: Dict[int, str] = {}

    def _call_once(lines: List[Tuple[int, Dict[str, str]]]) -> Dict[int, str]:
        # 軽量プロンプト
        # ルール：
        #  - 本文は絶対に変更しない
        #  - 半角/全角の違いは無視（全角化による誤検知を避ける）
        #  - 芸能人の名前の漢字間違いなど、テレビ上の不自然さのみ指摘
        #  - 誤りがある行のみ、"index[TAB]15文字以内の短い疑問形" で返す
        #  - OKな行は何も出力しない（空行も禁止）
        prompt_header = (
            "あなたはテレビ用ナレーションの校正者です。"
            "本文は一切変更しません。半角/全角の違いは無視して比較します。"
            "芸能人名など日本語の表記ミスやテレビで不自然な表現のみ簡潔に指摘してください。"
            "誤りがある行だけ出力、形式は「index<TAB>短い注意文(15文字以内,疑問形)」。"
            "OKな行は出力しない。余計な説明や囲みは禁止。"
        )

        # チャンク本文の用意（indexはチャンク内の0..Nで割当）
        lines_desc = []
        for local_i, (_, b) in enumerate(lines):
            # ここで半角/全角は無視していいが、送るのはそのまま
            # 入力は「[time] text」の簡素な形
            lines_desc.append(f"{local_i}\t[{b.get('time','').strip()}] {b.get('text','').strip()}")
        content = "以下の各行を個別に評価:\n" + "\n".join(lines_desc)

        nonlocal fallback_tried
        current_model = model_name
        for _ in range(2):
            try:
                resp = client.models.generate_content(
                    model=current_model,
                    contents=f"{prompt_header}\n\n{content}",
                )
                raw = (resp.text or "").strip()
                break
            except Exception:
                # 1回だけフォールバック
                if not fallback_tried:
                    current_model = "gemini-2.5-flash"
                    fallback_tried = True
                    continue
                raw = ""
                break

        result_map: Dict[int, str] = {}
        if not raw:
            return result_map

        # 期待出力：複数行 "index<TAB>notice"
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            if "\t" not in s:
                # 仕様違反の行は無視（堅牢化）
                continue
            idx_str, note = s.split("\t", 1)
            if not idx_str.isdigit():
                continue
            local_idx = int(idx_str)
            if not (0 <= local_idx < len(lines)):
                continue
            # 15文字・疑問形に整形
            note_final = _ensure_question_15ch(note)
            if note_final:
                global_idx = lines[local_idx][0]  # グローバル行index
                result_map[global_idx] = note_final
        return result_map

    # チャンクで順次実行
    for start in range(0, len(blocks), chunk_size):
        chunk = blocks[start:start + chunk_size]
        indexed_chunk = [(start + i, b) for i, b in enumerate(chunk)]
        partial = _call_once(indexed_chunk)
        notes_map.update(partial)

    return notes_map


# ===============================================================
# ▼▼▼ 本体：変換エンジン（あなたの既存ロジックを保持） ▼▼▼
# ===============================================================
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
                next_line = relevant_lines[i + 1].strip()
                next_normalized_line = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', next_line).translate(to_hankaku_time).replace('~', '-')
                if not re.match(time_pattern, next_normalized_line):
                    text_val = next_line
                    i += 1
            blocks.append({'time': time_val, 'text': text_val})
        i += 1

    output_lines = []

    # AIチェック用：元ブロックを保存（time/text）
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

    previous_end_hh = None

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
            elif previous_end_hh is not None and start_hh > previous_end_hh:
                should_insert_h_marker = True
                marker_hh_to_display = start_hh

        if should_insert_h_marker:
            output_lines.append("")
            output_lines.append(f"【{str(marker_hh_to_display).translate(to_zenkaku_num)}Ｈ】")
            output_lines.append("")

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

        # === N 話者の正規化（N/ｎ/Ｎ + 任意スペース を特別扱い）===
        speaker_symbol = 'Ｎ'
        text_content = block['text']
        body = ""

        # N の後にスペースが無くてもOK、N/n/Ｎ いずれも話者記号扱い
        n_head = re.match(r'^[NnＮ]\s*(.*)$', (text_content or ""))
        if n_head:
            # 先頭のN系は話者とみなし、残りを本文に
            body = n_head.group(1).strip()
        else:
            if n_force_insert_flag:
                # 既存ロジック（話者+本文の推定）
                match = re.match(r'^(\S+)\s+(.*)', text_content or "")
                if match:
                    raw_speaker = match.group(1)
                    body = (match.group(2) or "").strip()
                    if raw_speaker.upper() == 'N':
                        speaker_symbol = 'Ｎ'
                    else:
                        speaker_symbol = raw_speaker.translate(to_zenkaku_all)
                else:
                    # 話者記号なし → そのまま本文
                    body = text_content or ""
            else:
                speaker_symbol = ''
                body = text_content or ""

        if not body:
            body = "※注意！本文なし！"

        body = body.translate(to_zenkaku_all)

        end_string = ""
        add_blank_line = True

        if i + 1 < len(parsed_blocks):
            next_block = parsed_blocks[i + 1]
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

        if n_force_insert_flag and speaker_symbol:
            output_lines.append(f"{formatted_start_time}{spacer}{speaker_symbol}　{body}{end_string}")
        else:
            output_lines.append(f"{formatted_start_time}{spacer}{body}{end_string}")

        if add_blank_line and i < len(parsed_blocks) - 1:
            output_lines.append("")

    # 変換結果とAI用元データを返す
    return {"narration_script": "\n".join(output_lines), "ai_data": narration_blocks_for_ai}


# ===============================================================
# ▼▼▼ 画面（UI）：既存構成を維持 ▼▼▼
# ===============================================================
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

# --- 1段目 ---
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

# --- 2段目（左寄せ＋右スペース） ---
col1_bottom_opt, col2_bottom_opt, col3_bottom_opt, col4_bottom_spacer = st.columns([1.5, 2, 2, 9])

with col1_bottom_opt:
    n_force_insert = st.checkbox("N強制挿入", value=True)

with col2_bottom_opt:
    mm_ss_colon = st.checkbox("ｍｍ：ｓｓで出力", value=False)

with col3_bottom_opt:
    ai_check_flag = st.checkbox("誤字脱字チェックβ", value=False)

# 差分キャッシュ（セッション）初期化
if "ai_result_cache" not in st.session_state:
    st.session_state["ai_result_cache"] = {}      # {global_idx: note}
if "ai_input_digest" not in st.session_state:
    st.session_state["ai_input_digest"] = ""      # last digest string

# --- 3段目：変換＆表示 ---
if input_text:
    try:
        conversion_result = convert_narration_script(input_text, n_force_insert, mm_ss_colon)
        converted_text = conversion_result["narration_script"]
        ai_data = conversion_result["ai_data"]  # [{'time':..., 'text':...}, ...]

        # ===== AIチェック：オンの時だけ & 入力が変わっていた時だけAPI実行 =====
        final_text_for_display = converted_text  # まずはそのまま
        if ai_check_flag and GEMINI_API_KEY:
            digest_now = _digest_blocks(ai_data)

            # 入力が変わったらAI再実行
            need_requery = (digest_now != st.session_state["ai_input_digest"])
            if need_requery:
                ai_notes = check_blocks_with_gemini_fast(
                    ai_data,
                    api_key=GEMINI_API_KEY,
                    model_name="gemini-2.0-flash-lite-preview",  # 最速候補（不可なら内部で2.5-flashへフォールバック）
                    chunk_size=6
                )
                # キャッシュ保存
                st.session_state["ai_result_cache"] = ai_notes or {}
                st.session_state["ai_input_digest"] = digest_now

            # ここからはキャッシュを使って右側表示に「※短い注意」を差し込み
            # 変換結果を「行」ごとに対応づける：出力行と元ブロックのマッピングを作る
            # ルール：convert_narration_script は1ブロックにつき1行（＋場合により空行・H見出し行）
            # ここではシンプルに「非空＆'Ｈ】'でない本文行」をブロック順に数え上げ、ai_notesのindexに合わせて差し込む
            lines_out = converted_text.split("\n")
            block_line_indices = []  # 出力の中で本文行のインデックス一覧
            for idx, ln in enumerate(lines_out):
                s = ln.strip()
                if not s:
                    continue
                if s.startswith("【") and s.endswith("Ｈ】"):
                    # H見出しはスキップ
                    continue
                # それ以外の非空行を「本文行」とみなす
                block_line_indices.append(idx)

            # ai_result_cache: {global_block_idx: "※..."} を対象行の直下に差し込み
            # 直下挿入のため、後ろから処理するとインデックスがズレにくい
            ai_notes_cached = st.session_state.get("ai_result_cache", {})
            for g_idx in sorted(ai_notes_cached.keys(), reverse=True):
                if 0 <= g_idx < len(block_line_indices):
                    insert_at = block_line_indices[g_idx] + 1
                    note_line = "　　　　　　　　　" + ai_notes_cached[g_idx]  # 行頭に全角空白でインデント
                    lines_out.insert(insert_at, note_line)

            final_text_for_display = "\n".join(lines_out)

        # 右ペインに表示
        with col2_main:
            st.text_area("　コピーしてお使いください", value=final_text_for_display, height=500)

    except Exception as e:
        with col2_main:
            st.error(f"エラーが発生しました。テキストの形式を確認してください。\n\n詳細: {e}")
            st.text_area("　コピーしてお使いください", value="", height=500, disabled=True)

else:
    # 入力なし時の高さダミー
    with col2_main:
        st.markdown('<div style="height: 500px;"></div>', unsafe_allow_html=True)

# --- フッター ---
st.markdown("---")
st.markdown(
    """
    <div style="text-align: right; font-size: 12px; color: #C5D6B9;">
        © 2025 kimika Inc. All rights reserved.
    </div>
    """,
    unsafe_allow_html=True
)
