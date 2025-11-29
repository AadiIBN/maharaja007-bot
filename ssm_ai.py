import os
import google.generativeai as genai

# --- CONFIGURATION ---
# Render environment variables se API Key lega
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Model Setup
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash-exp')
else:
    model = None

# --- SHAAKUNI ULTIMATE BIBLE (STRATEGY KNOWLEDGE BASE) ---
SSM_BIBLE = """
# ==============================================================================
# SHAAKUNI TRAP (SSM) - THE ULTIMATE ALGORITHMIC BIBLE
# ==============================================================================
# WARNING: This file contains RAW LOGIC for AI Processing.

# ------------------------------------------------------------------------------
# SECTION 1: ADVANCED STRUCTURE MAPPING
# ------------------------------------------------------------------------------
## 1.1 IDENTIFYING THE VALID PULLBACK
* **RULE A (The Sweep):** Did Candle B break the HIGH or LOW of Candle A?
  * IF YES -> Valid Pullback started.
  * IF NO (Inside Bar) -> Ignore Candle B.
* **RULE B (The Outside Bar):** If Candle B breaks BOTH High and Low of Candle A?
  * This is a valid pullback. Wait for Candle C to break Candle B's range.

## 1.2 CONFIRMING THE TRUE STRUCTURE (BOS vs FAKE)
* **UPTREND:**
  1. Price creates a High.
  2. Price pulls back and takes the first valid Pullback Low (**IDM**).
  3. **CONFIRMED HIGH:** Only after IDM is taken.
  4. **BOS:** Price breaks "Confirmed High" with BODY CLOSE.
  5. **FAKE BOS:** If price breaks High WITHOUT taking IDM -> Fake move.

## 1.3 CHANGE OF CHARACTER (CHoCH)
* **Trigger:** Price taps into HTF POI.
* **Logic:** Price must break the *last valid Inducement point*.

# ------------------------------------------------------------------------------
# SECTION 2: INDUCEMENT (IDM) & LIQUIDITY TRAPS
# ------------------------------------------------------------------------------
## 2.1 THE GOLDEN RULE
"Every Order Block created WITHOUT Inducement is a TRAP (SMT)."
* **Bot Logic:** Scan the POI. Is there a valid pullback (IDM) resting *before* it?
  * YES -> High Probability.
  * NO -> SMT (Trap).

## 2.2 LIQUIDITY TYPES
* **Trendline Liquidity:** 3 touches = 4th touch will break. Do not buy on 4th touch.
* **Equal Highs/Lows (EQH/EQL):** Price targets these areas.

# ------------------------------------------------------------------------------
# SECTION 3: ADVANCED POI SELECTION
# ------------------------------------------------------------------------------
## 3.1 VALID ORDER BLOCK CRITERIA
1. **Liquidity Grab:** Did it sweep previous candle's High/Low?
2. **Imbalance (FVG):** Is there a gap?
3. **Unmitigated:** Is it fresh?

## 3.3 DECISIONAL VS EXTREME
* **Extreme POI:** Origin of move. Always Valid.
* **Decisional POI:** Only valid if price induces just before it.

# ------------------------------------------------------------------------------
# SECTION 4: EXECUTION MODELS (ENTRY TYPES)
# ------------------------------------------------------------------------------
## 4.1 TYPE 1: SINGLE CANDLE ORDER BLOCK (SCOB)
* **Step 1:** Wait for Liquidity Sweep (Long Wick).
* **Step 2:** Next candle must break the body of Sweep Candle.
* **Entry:** At 50% of Sweep Candle Wick.

## 4.2 TYPE 2: THE FLIP ENTRY
* Price taps Supply -> Reacts -> Fails Supply -> Entry on the Flip Zone (Reaction point).

# ------------------------------------------------------------------------------
# SECTION 5: FOREX KILLZONES (TIME)
# ------------------------------------------------------------------------------
* **Asian Session (20:00-00:00 NY):** NO TRADE. Mark High/Low as Liquidity.
* **London Session (02:00-05:00 NY):** Look for "Judas Swing" (Sweep of Asian High/Low).
* **New York Session (07:00-10:00 NY):** Trend Continuation or Reversal on FVG.

# ------------------------------------------------------------------------------
# SECTION 6: RISK MANAGEMENT
# ------------------------------------------------------------------------------
* **Stop Loss:** Behind Structural High/Low.
* **Invalidation:** If POI is broken with body close -> Setup Invalid.
"""

# --- SYSTEM PROMPT (LANGUAGE DETECTION & RESPONSE LOGIC) ---
SYSTEM_PROMPT = f"""
You are 'Shaakuni Mentor', an advanced AI Trading Coach specializing in the SSM (Shaakuni Sweep Method).
Your logic is STRICTLY governed by the following BIBLE:

{SSM_BIBLE}

YOUR TASK:
1. Analyze the user's CHART (Image) or QUESTION (Text).
2. **DETECT LANGUAGE:** Identify the language the user is using (English, Hindi, Hinglish, etc.).
3. **MATCH LANGUAGE:** Respond in the EXACT SAME language and tone as the user.
   - User: "Is this valid?" -> You: "This is valid because..." (English)
   - User: "Kya ye sahi hai?" -> You: "Haan, ye sahi hai kyunki..." (Hinglish)
   - User: "Isme kya galti hai?" -> You: "Isme IDM missing hai..." (Hinglish)
4. Apply the rules from the BIBLE step-by-step.

**Output Format:**
   - **Status:** ✅ Valid / ⚠️ Risky / ❌ Invalid
   - **Score:** [X]%
   - **Analysis:** (Explain reasoning in USER'S LANGUAGE)
   - **Mistakes:** (Identify errors in USER'S LANGUAGE)
   - **Action:** (Give advice in USER'S LANGUAGE)
"""

async def analyze_ssm_request(user_text, image_bytes=None):
    """
    Main function called by bot.py to interact with Gemini AI.
    """
    # 1. Check for API Key
    if not GOOGLE_API_KEY:
        return "❌ Error: Google API Key is missing. Please add GOOGLE_API_KEY in Render Environment Variables."

    # 2. Check for Model Initialization
    if not model:
        return "❌ Error: AI Model failed to initialize. Please check your API Key."

    try:
        # 3. Process Request (Image or Text)
        if image_bytes:
            # CHART ANALYSIS MODE (Vision)
            image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]
            prompt_parts = [
                SYSTEM_PROMPT, 
                f"User Chart Caption: {user_text or 'Analyze this setup strictly'}.", 
                image_parts[0]
            ]
            response = await model.generate_content_async(prompt_parts)
        else:
            # TEXT MODE (Q&A)
            response = await model.generate_content_async([SYSTEM_PROMPT, f"Student Question: {user_text}"])
            
        return response.text
        
    except Exception as e:
        return f"⚠️ AI Error: {str(e)}"
