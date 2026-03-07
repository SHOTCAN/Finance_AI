"""
OCR Module — Receipt Scanning (Groq Vision)
=============================================
- Resize images before sending to Groq Vision API
- Groq Vision AI → text extraction + structured parsing in one step
- No Google Cloud billing required — uses existing Groq API keys
"""

import io
import base64
import json
from datetime import date, datetime
from typing import Optional

from PIL import Image

from app.config import settings


# ============================================
# IMAGE PREPROCESSING
# ============================================

def resize_image(image_bytes: bytes, max_width: int = 1024, max_height: int = 1024) -> bytes:
    """
    Resize image to reduce API usage.
    Converts to JPEG for smaller size.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))

        # Convert to RGB if needed (e.g., RGBA/PNG)
        if img.mode in ('RGBA', 'P', 'LA'):
            img = img.convert('RGB')

        # Resize if too large
        if img.width > max_width or img.height > max_height:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

        # Save as JPEG
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=85, optimize=True)
        return buffer.getvalue()
    except Exception as e:
        print(f"[OCR] Image resize error: {e}")
        return image_bytes  # Return original if resize fails


def validate_image(image_bytes: bytes) -> dict:
    """Validate image before OCR processing."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size
        size_kb = len(image_bytes) / 1024

        if size_kb > 10240:  # 10MB
            return {'valid': False, 'error': 'File terlalu besar (max 10MB)'}
        if width < 100 or height < 100:
            return {'valid': False, 'error': 'Gambar terlalu kecil'}

        return {'valid': True, 'width': width, 'height': height, 'size_kb': round(size_kb)}
    except Exception as e:
        return {'valid': False, 'error': f'Format gambar tidak valid: {e}'}


# ============================================
# GROQ VISION OCR + PARSING (ALL-IN-ONE)
# ============================================

async def extract_and_parse_receipt(image_bytes: bytes) -> dict:
    """
    Use Groq Vision AI to extract text AND parse receipt data in one step.
    Sends base64-encoded image to llama-3.2-90b-vision-preview.
    Returns parsed receipt data or error.
    """
    from app.modules.ai_processing.groq_rotator import groq_rotator

    try:
        # Resize image first
        resized = resize_image(image_bytes)
        print(f"[OCR] Image resized: {len(image_bytes)} → {len(resized)} bytes")

        # Convert to base64
        b64_image = base64.b64encode(resized).decode('utf-8')
        print(f"[OCR] Base64 encoded: {len(b64_image)} chars")

        # Build vision message
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}"
                        }
                    },
                    {
                        "type": "text",
                        "text": (
                            "Kamu adalah parser struk belanja. Dari gambar struk ini, "
                            "extract informasi berikut dalam format JSON:\n"
                            "{\n"
                            '  "merchant": "nama toko/merchant",\n'
                            '  "date": "YYYY-MM-DD",\n'
                            '  "total": angka total (integer, tanpa simbol mata uang/titik/koma),\n'
                            '  "items": ["item1", "item2"],\n'
                            '  "category": "salah satu dari: Makanan, Minuman, Belanja, '
                            'Kesehatan, Transportasi, Tagihan, Rumah Tangga, Lainnya",\n'
                            '  "currency": "mata uang yang terdeteksi (IDR/USD/EUR/CHF dll)",\n'
                            '  "ocr_text": "teks lengkap yang terbaca dari struk (maks 200 karakter)"\n'
                            "}\n"
                            "Jika tidak bisa menentukan field, isi null.\n"
                            "Jika total dalam mata uang asing, konversi ke IDR (Rupiah) dengan kurs perkiraan.\n"
                            "PENTING: Jawab HANYA dengan JSON, tanpa teks lain."
                        ),
                    },
                ],
            }
        ]

        # Use vision model
        result = await groq_rotator.chat(
            messages,
            model="llama-3.2-90b-vision-preview",
            max_tokens=500,
            temperature=0.1,
        )

        if not result['success']:
            print(f"[OCR] ❌ Groq Vision failed: {result.get('error', 'Unknown')}")
            # Try with smaller model as fallback
            err_msg1 = result.get('error', 'Unknown')
            result = await groq_rotator.chat(
                messages,
                model="llama-3.2-11b-vision-preview",
                max_tokens=500,
                temperature=0.1,
            )
            if not result['success']:
                err_msg2 = result.get('error', 'Unknown')
                return {'success': False, 'error': f'AI Vision gagal: {err_msg2} (90b: {err_msg1})'}

        print(f"[OCR] ✅ Groq Vision response received (key #{result.get('key_used', '?')})")

        # Parse JSON response
        content = result['content'].strip()
        # Clean up potential markdown code blocks
        if content.startswith('```'):
            content = content.split('\n', 1)[1].rsplit('```', 1)[0]

        parsed = json.loads(content)

        # Validate parsed data
        validated = {
            'merchant': parsed.get('merchant'),
            'date': None,
            'total': None,
            'items': parsed.get('items', []),
            'category': parsed.get('category', 'Lainnya'),
            'ocr_text_preview': parsed.get('ocr_text', '')[:200],
        }

        # Validate date
        if parsed.get('date'):
            try:
                validated['date'] = datetime.strptime(parsed['date'], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                validated['date'] = date.today()
        else:
            validated['date'] = date.today()

        # Validate total
        if parsed.get('total') is not None:
            try:
                total = float(str(parsed['total']).replace(',', '').replace('.', ''))
                if 0 < total < 100_000_000:  # Reasonable range
                    validated['total'] = total
            except (ValueError, TypeError):
                pass

        if validated['total'] is None:
            return {'success': False, 'error': 'Tidak bisa menentukan total dari struk'}

        validated['success'] = True
        print(f"[OCR] ✅ Parsed: {validated['merchant']} — Rp {validated['total']:,.0f}")
        return validated

    except json.JSONDecodeError as e:
        print(f"[OCR] ❌ JSON parse error: {e}")
        return {'success': False, 'error': 'Gagal parse response AI'}
    except Exception as e:
        print(f"[OCR] ❌ Vision error: {type(e).__name__}: {e}")
        return {'success': False, 'error': f'Error: {e}'}


# ============================================
# FULL OCR PIPELINE
# ============================================

async def process_receipt(image_bytes: bytes) -> dict:
    """
    Full receipt processing pipeline:
    1. Validate image
    2. Groq Vision AI: OCR + parse in one step
    3. Validation
    """
    # Step 1: Validate
    validation = validate_image(image_bytes)
    if not validation['valid']:
        return {'success': False, 'error': validation['error']}

    # Step 2-3: Groq Vision OCR + Parse
    result = await extract_and_parse_receipt(image_bytes)
    return result
