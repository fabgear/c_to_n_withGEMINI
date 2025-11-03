import streamlit as st
import re
import math

# ============================
# Gemini SDK アダプタ（最小追加）
# ============================
_GEMINI_MODE = None  # "genai" (google-genai) or "generativeai" (google-generativeai)
try:
    # 新SDK
    from google import genai as _genai_new
    from google.genai.errors import APIError as _GenAIError
    _GEMINI_MODE = "genai"
except Exception:
    try:
        # 旧SDK
        import google.generativeai as _genai_old
        _GEMINI_MODE = "generativeai"
        class _GenAIError(Exception):
            pass
    except Exception:
        _GEMINI_MODE = None
        class _GenAIError(Exception):
            pass

def _gemini_generate_text(prompt: str, api_key: str, model_hint: str = None) -> str:
    """
    SDKの違いを吸収してテキストを返す。失敗時は例外送出。
    """
    # モデル優先順位
    candidates = []
    if model_hint:
        candidates.append(model_hint)
    candidates.extend(["gemini-2.5-flash", "gemini-1.5-flash-8b"])

    if _GEMINI_MODE == "genai":
        # google-genai
        from google.genai.types import GenerateContentConfig
        client = _genai_new.Client(api_key=api_key)
        last_err = None
        for m in candidates:
            try:
                resp = client.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=GenerateContentConfig()
                )
                return getattr(resp, "text", "").strip()
            except Exception as e:
                last_err = e
        raise _GenAIError(last_err)
    elif _GEMINI_MODE == "generativeai":
        # google-generativeai
        _genai_old.configure(api_key=api_key)
        last_err = None
        for m in candidates:
            try:
                model = _genai_old.GenerativeModel(m)
                resp = model.generate_content(prompt)
                return getattr(resp, "text", "").strip()
            except Exception as e:
                last_err = e
        raise _GenAIError(last_err)
    else:
        raise _GenAIError("Gemini SDK が見つかりません。requirements.txt に 'google-genai' または 'google-generativeai' を追加してください。")

# ============================
# 校正（ver0.01の仕様に合わせる）
# ============================
def check_narration_with_gemini(narration_blocks, api_key, model_from_secrets=None):
    """
    narration_blocks: [{'time': '00;00;10;00 - 00;00;12;20', 'text': 'N ほげ'} ...]
    返却: Markdown文字列（表 or 問題なし）
    """
    if not api_key:
        return "エラー：Gemini APIキーが設定されていません。Streamlit Secretsを確認してください。"

    formatted_text = "\n".join([f"[{b['time']}] {b['text']}" for b in narration_blocks])

    prompt = f"""
あなたはプロフェッショナルな校正者です。
以下のナレーション原稿のリストを、誤字脱字・不適切な表現・文法ミスがないか厳密にチェックしてください。

【指示】
1. 入力本文は一切改変せずに、誤りがある箇所のみを指摘してください。
2. 指摘がある場合のみ、次の Markdown テーブルで出力してください。無ければ「問題ありませんでした。」のみ。
3. 指摘は最小限（意味が変わる修正は不可）。

【出力形式】
| 原文の位置 | 本文 | 修正提案 | 理由 |
|---|---|---|---|
| (行番号/箇所) | (誤っている語句) | (正しい語句) | (理由) |

【ナレーション原稿】
---
{formatted_text}
---
""".strip()

    try:
        text = _gemini_generate_text(prompt, api_key, model_hint=model_from_secrets)
        return text if text else "問題ありませんでした。"
    except _GenAIError as e:
        return f"Gemini APIエラー: {e}"
    except Exception as e:
        return f"予期せぬエラー: {e}"

# ============================
# 以降は ver0.01 のロジックそのまま
# ============================
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
        return {"narration_script": "エラー：変換可能なタイムコード（フレーム情報を含む形式）が見つかりませんでした。", "ai_data": []}
