import os
from openai import OpenAI
from io import BytesIO
import base64
import asyncio

# --- CONFIGURATION ---
# Render environment variables se API Key lega (Ab naam OPENAI_API_KEY hoga)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Model Setup
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
    # Vision model for chart analysis
    AI_MODEL = 'gpt-4o-mini' 
else:
    client = None
    AI_MODEL = None

# --- SHAAKUNI ULTIMATE BIBLE (STRATEGY KNOWLEDGE BASE) ---
# Ye knowledge base wahi rahegi, taki ChatGPT aapke rules ko follow kare.
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
2. **DETECT LANGUAGE:** Identify the language the user is using (English, Hindi, Hinglish, Spanish, etc.).
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

# Function to convert image bytes to base64 string
def get_base64_image(image_bytes):
    return base64.b64encode(image_bytes).decode("utf-8")

async def analyze_ssm_request(user_text, image_bytes=None):
    """
    Main function called by bot.py to interact with OpenAI API.
    """
    if not client:
        return "❌ Error: OpenAI API Key is missing. Please add OPENAI_API_KEY in Render Environment Variables."

    # Convert image to base64 for API call
    base64_image = None
    if image_bytes:
        base64_image = get_base64_image(image_bytes)

    # --- Construct Message Content ---
    prompt_messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    
    user_content = []
    
    # 1. Add Text Prompt
    user_content.append({"type": "text", "text": f"User Query: {user_text or 'Analyze the chart.'}"})
    
    # 2. Add Image if available
    if base64_image:
        user_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{base64_image}",
                "detail": "high"
            }
        })

    prompt_messages.append({"role": "user", "content": user_content})

    try:
        # Call the OpenAI API (Using sync call since analyze_ssm_request is called from an async handler)
        loop = asyncio.get_event_loop()
        
        response = await loop.run_in_executor(None, lambda: client.chat.completions.create(
            model=AI_MODEL,
            messages=prompt_messages,
            temperature=0.2,
            max_tokens=1500
        ))
        
        return response.choices[0].message.content
        
    except Exception as e:
        # Check if the error is due to a rate limit or invalid API key
        if "rate limit" in str(e).lower() or "authentication" in str(e).lower():
            return f"❌ API Authentication Error or Rate Limit Exceeded. Please check your OPENAI_API_KEY and billing details. Error: {str(e)}"
        return f"⚠️ AI Processing Error: {str(e)}"
