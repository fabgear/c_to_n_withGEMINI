import streamlit as st
import re
import math
# ▼▼▼【ver5.4 変更点】Gemini API関連のインポートを追加 (エラー回避ロジック付き) ▼▼▼
try:
    from google import genai
    from google.genai.errors import APIError
except ImportError:
    class APIError(Exception): pass
    class MockClient:
        def __init__(self, *args, **kwargs): pass
        def models(self): return self
        def generate_content(self, *args, **kwargs): return lambda: type('Response', (object,), {'text': 'ライブラリ google-genai が未インストールです。'})()
    genai = MockClient()
    
# check_narration_with_gemini 関数はver5.3と同一
def check_narration_with_gemini(narration_blocks, api_key):
    """Gemini APIを使用してナレーションの誤字脱字をチェックする"""
    if not api_key:
        return "エラー：Gemini APIキーが設定されていません。Streamlit Secretsを確認してください。"

    try:
        client = genai.Client(api_key=api_key)
        formatted_text = "\n".join([f"[{b['time']}] {b['text']}" for b in narration_blocks])
        prompt = f"""
        あなたはプロフェッショナルな校正者です。
        以下のナレーション原稿のリストを、誤字脱字、不適切な表現、文法ミスがないか厳密にチェックしてください。
        
        【指示】
        1. 入力された本文（「本文」カラムの内容）は**一切変更しないで**ください。
        2. 誤りが見つかった場合のみ、以下の Markdown テーブル形式で修正提案とその理由を出力してください。
        3. 誤りがない場合は、「問題ありませんでした。」とだけ出力してください。
        
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


# convert_narration_script 関数はver5.3と同一
def convert_narration_script(text, n_force_insert_flag=True, mm_ss_colon_flag=False):
    # （中略：ロジックはver4.4と同一）
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
            
    if start_index == -1: return "エラー：変換可能なタイムコード（フレーム情報を含む形式）が見つかりませんでした。"
        
    relevant_lines = lines[start_index:]

    blocks = []
    i = 0
    while i < len(relevant_lines):
        current_line = relevant_lines[i].strip()
        line_with_frames = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', current_line)
        normalized_line = line_with_frames.translate(to_hankaku_time).replace('~', '-')

        if re.match(time_pattern, normalized_line):
            time_val = current_line; text_val = ""
            if i + 1 < len(relevant_lines):
                next_line = relevant_lines[i+1].strip()
                next_normalized_line = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', next_line).translate(to_hankaku_time).replace('~', '-')
                if not re.match(time_pattern, next_normalized_line):
                    text_val = next_line; i += 1
            blocks.append({'time': time_val, 'text': text_val})
        i += 1
        
    output_lines = []
    
    narration_blocks_for_ai = [] # AI用ブロックリストを定義

    parsed_blocks = []
    for block in blocks:
        line_with_frames = re.sub(r'(\d{2}:\d{2}:\d{2})(?![:.]\d{2})', r'\1.00', block['time'])
        normalized_time_str = line_with_frames.translate(to_hankaku_time).replace('~', '-')
        time_match = re.match(time_pattern, normalized_time_str)
        if not time_match: continue
        
        groups = time_match.groups()
        start_hh, start_mm, start_ss, start_fr, end_hh, end_mm, end_ss, end_fr = [int(g or 0) for g in groups]
        
        # AIチェック用に元のテキストブロックを格納
        narration_blocks_for_ai.append({
            'time': block['time'].strip(),
            'text': block['text'].strip()
        })
        
        parsed_blocks.append({
            'start_hh': start_hh, 'start_mm': start_mm, 'start_ss': start_ss, 'start_fr': start_fr,
            'end_hh': end_hh, 'end_mm': end_mm, 'end_ss': end_ss, 'end_fr': end_fr,
            'text': block['text']
        })

    previous_end_hh = -1

    for i, block in enumerate(parsed_blocks):
        start_hh, start_mm, start_ss, start_fr = block['start_hh'], block['start_mm'], block['start_ss'], block['start_fr']
        end_hh, end_mm, end_ss, end_fr = block['end_hh'], block['end_mm'], block['end_ss'], block['end_fr']

        should_insert_h_marker = False
        marker_hh_to_display = -1
        
        if i == 0:
            if start_hh > 0: should_insert_h_marker = True; marker_hh_to_display = start_hh
            previous_end_hh = end_hh 
        else:
            if start_hh < end_hh: should_insert_h_marker = True; marker_hh_to_display = end_hh 
            elif start_hh > previous_end_hh: should_insert_h_marker = True; marker_hh_to_display = start_hh 

        if should_insert_h_marker:
             output_lines.append("")
             output_lines.append(f"【{str(marker_hh_to_display).translate(to_zenkaku_num)}Ｈ】")
             output_lines.append("")
             
        previous_end_hh = end_hh 

        total_seconds_in_minute_loop = (start_mm % 60) * 60 + start_ss
        spacer = ""
        
        is_half_time = False # 「半」判定フラグ
        base_time_str = "" # MMSSの数字部分を格納する変数
        
        # 1. MMSS の基本形とspacerを決定
        if 0 <= start_fr <= 9:
            display_mm = (total_seconds_in_minute_loop // 60) % 60; display_ss = total_seconds_in_minute_loop % 60
            base_time_str = f"{display_mm:02d}{display_ss:02d}"
            spacer = "　　　"
        elif 10 <= start_fr <= 22:
            display_mm = (total_seconds_in_minute_loop // 60) % 60; display_ss = total_seconds_in_minute_loop % 60
            base_time_str = f"{display_mm:02d}{display_ss:02d}"
            spacer = "　　"
            is_half_time = True # 半フラグON
        else:
            total_seconds_in_minute_loop += 1
            display_mm = (total_seconds_in_minute_loop // 60) % 60; display_ss = total_seconds_in_minute_loop % 60
            base_time_str = f"{display_mm:02d}{display_ss:02d}"
            spacer = "　　　"

        # 2. 最終的なformatted_start_timeの決定ロジックを統合
        # base_time_str (MMSS) にコロンを挿入
        if mm_ss_colon_flag:
            mm_part = base_time_str[:2]; ss_part = base_time_str[2:]
            colon_time_str = f"{mm_part}：{ss_part}"
        else:
            colon_time_str = base_time_str

        # 「半」を最後に追加
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
                raw_speaker = match.group(1); body = match.group(2).strip()
                if raw_speaker.upper() == 'N': speaker_symbol = 'Ｎ'
                else: speaker_symbol = raw_speaker.translate(to_zenkaku_all)
            else:
                if text_content.upper() == 'N' or text_content == 'Ｎ': body = ""
                elif text_content.startswith('Ｎ '): body = text_content[2:].strip()
                elif text_content.startswith('N '): body = text_content[2:].strip()
                else: body = text_content
            if not body: body = "※注意！本文なし！"
        else:
            speaker_symbol = '' # 話者記号は空
            body = text_content # 元のテキスト全体を本文として扱う
            
            # 本文が空（または空白のみ）の場合、警告を出す
            if not body.strip():
                body = "※注意！本文なし！"

        body = body.translate(to_zenkaku_all)
        
        end_string = ""; add_blank_line = True
        
        if i + 1 < len(parsed_blocks):
            next_block = parsed_blocks[i+1]
            end_total_seconds = (end_hh * 3600) + (end_mm * 60) + end_ss + (end_fr / FRAME_RATE)
            next_start_total_seconds = (next_block['start_hh'] * 3600) + (next_block['start_mm'] * 60) + next_block['start_ss'] + (next_block['start_fr'] / FRAME_RATE)
            if next_start_total_seconds - end_total_seconds < CONNECTION_THRESHOLD:
                add_blank_line = False

        if add_blank_line:
            adj_ss = end_ss; adj_mm = end_mm
            if 0 <= end_fr <= 9: adj_ss = end_ss - 1; 
            if adj_ss < 0: adj_ss = 59; adj_mm -= 1
            
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
            
    return {"narration_script": "\n".join(output_lines), "ai_data": narration_blocks_for_ai} # 戻り値を変更

# ===============================================================
# ▼▼▼ Streamlitの画面を作る部分 - （ver5.4：UI安定化と機能統合）▼▼▼
# ===============================================================
st.set_page_config(page_title="Caption to Narration", page_icon="📝", layout="wide")
st.title('Caption to Narration')

# Streamlit Cloud で Secrets から API キーを取得
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "")

st.markdown("""<style> 
textarea::placeholder { 
    font-size: 13px;
} 
textarea {
    font-size: 14px !important;
}
</style>""", unsafe_allow_html=True)

# ----------------------------------------------------------------------------------
# 0. help_textの定義
# ----------------------------------------------------------------------------------
help_text = """
【機能詳細】  
・ENDタイム(秒のみ)が自動で入ります  
　分をまたぐ時は(分秒)、次のナレーションと繋がる時は割愛されます  
・頭の「N」は自動で全角に変換され未記載の時は自動挿入されます  
　VOや実況などN以外はそのまま適応されます  
・Hをまたぐときは自動で仕切りが入ります  
・ナレーション本文の半角英数字は全て全角に変換します  
"""

# ----------------------------------------------------------------------------------
# 1段目：メインのテキストエリアとタイトル
# ----------------------------------------------------------------------------------
col1_top, col2_top = st.columns(2)

# タイトルはテキストエリアと同一カラムの最上部に配置 (ver2構造)
with col1_top:
    st.header('')
with col2_top:
    st.header('')


# テキストエリアの定義と結果の表示を同じブロックで行う
col1_main, col2_main = st.columns(2)

# st.text_areaの戻り値をここで定義
input_text = ""

with col1_main:
    # input_textの定義
    input_text = st.text_area(
        "ここに元原稿をペースト", 
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

# ----------------------------------------------------------------------------------
# 2段目：コントロールエリア（3カラム構造）
# ----------------------------------------------------------------------------------
# 3つのカラムを定義：[N強制挿入] [MM:SSで出力] [空]
col1_bottom_opt, col2_bottom_opt, col3_bottom_opt = st.columns([3, 4, 6]) 

# ▼▼▼【ver5.4 修正点】チェックボックスの横並びを3カラムで実現（構造をver5.2の形に戻す） ▼▼▼
with col1_bottom_opt:
    n_force_insert = st.checkbox("N強制挿入", value=True)

with col2_bottom_opt:
    mm_ss_colon = st.checkbox("ｍｍ：ｓｓで出力", value=False)
    
# col3_bottom_opt に AI チェックボックスを配置
with col3_bottom_opt:
    ai_check_flag = st.checkbox("誤字脱字をAIでチェック", value=False)
# ▲▲▲【ver5.4 修正点】ここまで ▼▼▼


# ----------------------------------------------------------------------------------
# 3. 変換結果の表示（メインロジック）
# ----------------------------------------------------------------------------------
if input_text:
    try:
        # 変換関数を実行し、結果（スクリプトとAI用データ）を取得
        conversion_result = convert_narration_script(input_text, n_force_insert, mm_ss_colon)
        converted_text = conversion_result["narration_script"]
        ai_data = conversion_result["ai_data"]
        
        # output_text_area を col2_main の中で呼び出す
        with col2_main:
             st.text_area("　コピーしてお使いください", value=converted_text, height=500)
             
        # AIチェックロジック
        if ai_check_flag:
            st.markdown("---") # 区切り線
            st.subheader("📝 AI校正チェック結果")
            
            # APIキーがない場合の警告
            if not GEMINI_API_KEY:
                 st.error("エラー: AI校正機能を利用するには、Streamlit Secretsに 'GEMINI_API_KEY' を設定してください。")
            elif isinstance(genai, MockClient):
                 st.error("エラー: AI校正機能のライブラリが見つかりません。requirements.txtに 'google-genai' を追加してください。")
            else:
                 with st.spinner("Geminiが誤字脱字をチェック中..."):
                     ai_result_text = check_narration_with_gemini(ai_data, GEMINI_API_KEY)
                     st.markdown(ai_result_text) # Markdownとして表示（テーブルが見やすくなる）
        
    except Exception as e:
        # エラー時
        with col2_main:
            st.error(f"エラーが発生しました。テキストの形式を確認してください。\n\n詳細: {e}")
            st.text_area("　コピーしてお使いください", value="", height=500, disabled=True)
            
# --- フッターをカスタマイズ ---
st.markdown("---")
st.markdown(
    """
    <div style="text-align: right; font-size: 12px; color: #C5D6B9;">
        © 2025 kimika Inc. All rights reserved.
    </div>
    """,
    unsafe_allow_html=True
)
