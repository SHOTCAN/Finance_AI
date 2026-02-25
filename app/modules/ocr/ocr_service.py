"""
OCR Module — Receipt Scanning
==============================
- Resize images before sending to Vision API (reduce quota usage)
- Google Cloud Vision OCR → text extraction
- Groq LLM structured extraction (merchant, date, amount, category)
- Validation before storage
"""

import io
from datetime import date, datetime
from typing import Optional

from PIL import Image

from app.config import settings


# ============================================
# IMAGE PREPROCESSING
# ============================================

def resize_image(image_bytes: bytes, max_width: int = 1024, max_height: int = 1024) -> bytes:
    """
    Resize image to reduce Vision API quota usage.
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
# GOOGLE VISION OCR
# ============================================

async def extract_text_from_image(image_bytes: bytes) -> Optional[str]:
    """
    Use Google Cloud Vision API to extract text from receipt image.
    Image is resized first to save quota.
    Supports credentials from JSON string (cloud) or file path (local).
    """
    if not settings.GOOGLE_VISION_ENABLED:
        print("[OCR] Google Vision is DISABLED (GOOGLE_VISION_ENABLED=False)")
        return None

    try:
        from google.cloud import vision
        from google.oauth2 import service_account
        import json as _json
        import os

        # Step 1: Load credentials
        credentials = None
        print(f"[OCR] Loading credentials...")
        print(f"[OCR]   GOOGLE_SERVICE_ACCOUNT_JSON set: {bool(settings.GOOGLE_SERVICE_ACCOUNT_JSON)}")
        print(f"[OCR]   GOOGLE_SERVICE_ACCOUNT_JSON length: {len(settings.GOOGLE_SERVICE_ACCOUNT_JSON)}")
        print(f"[OCR]   GOOGLE_SERVICE_ACCOUNT_FILE: {settings.GOOGLE_SERVICE_ACCOUNT_FILE}")
        print(f"[OCR]   File exists: {os.path.exists(settings.GOOGLE_SERVICE_ACCOUNT_FILE)}")

        if settings.GOOGLE_SERVICE_ACCOUNT_JSON:
            try:
                info = _json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
                credentials = service_account.Credentials.from_service_account_info(info)
                print(f"[OCR]   ✅ Loaded from JSON env var (project: {info.get('project_id', '?')})")
            except Exception as e:
                print(f"[OCR]   ❌ Failed to parse JSON env var: {e}")
                return None
        elif os.path.exists(settings.GOOGLE_SERVICE_ACCOUNT_FILE):
            credentials = service_account.Credentials.from_service_account_file(
                settings.GOOGLE_SERVICE_ACCOUNT_FILE
            )
            print(f"[OCR]   ✅ Loaded from file")
        else:
            print(f"[OCR]   ❌ No credentials available!")
            print(f"[OCR]   Set GOOGLE_SERVICE_ACCOUNT_JSON env var on Railway")
            return None

        # Step 2: Resize and call Vision API
        client = vision.ImageAnnotatorClient(credentials=credentials)
        resized = resize_image(image_bytes)
        print(f"[OCR] Image resized: {len(image_bytes)} → {len(resized)} bytes")

        image = vision.Image(content=resized)
        response = client.text_detection(image=image)

        if response.error.message:
            print(f"[OCR] ❌ Vision API error: {response.error.message}")
            return None

        texts = response.text_annotations
        if texts:
            result_text = texts[0].description
            print(f"[OCR] ✅ Text found: {len(result_text)} chars")
            return result_text
        
        print("[OCR] ⚠️ No text found in image")
        return None

    except ImportError as e:
        print(f"[OCR] ❌ Import error: {e}")
        return None
    except Exception as e:
        print(f"[OCR] ❌ Vision error: {type(e).__name__}: {e}")
        return None


# ============================================
# LLM STRUCTURED EXTRACTION
# ============================================

async def parse_receipt_text(ocr_text: str) -> dict:
    """
    Use Groq LLM to parse OCR text into structured receipt data.
    LLM handles reasoning/extraction, not numeric computation.
    """
    from app.modules.ai_processing.groq_rotator import groq_rotator

    messages = [
        {
            "role": "system",
            "content": (
                "Kamu adalah parser struk belanja. Dari teks OCR di bawah, "
                "extract informasi berikut dalam format JSON:\n"
                "{\n"
                '  "merchant": "nama toko/merchant",\n'
                '  "date": "YYYY-MM-DD",\n'
                '  "total": angka total (integer, tanpa Rp/titik/koma),\n'
                '  "items": ["item1", "item2"],\n'
                '  "category": "salah satu dari: Makanan, Minuman, Belanja, '
                'Kesehatan, Transportasi, Tagihan, Rumah Tangga, Lainnya"\n'
                "}\n"
                "Jika tidak bisa menentukan field, isi null.\n"
                "PENTING: Jawab HANYA dengan JSON, tanpa teks lain."
            ),
        },
        {"role": "user", "content": f"Teks OCR struk:\n\n{ocr_text[:2000]}"},
    ]

    result = await groq_rotator.chat(messages, max_tokens=300, temperature=0.1)

    if not result['success']:
        return {'success': False, 'error': 'AI tidak tersedia'}

    try:
        import json
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
        return validated

    except (json.JSONDecodeError, Exception) as e:
        return {'success': False, 'error': f'Gagal parse response AI: {e}'}


# ============================================
# FULL OCR PIPELINE
# ============================================

async def process_receipt(image_bytes: bytes) -> dict:
    """
    Full receipt processing pipeline:
    1. Validate image
    2. Resize for quota savings
    3. OCR text extraction (Vision API)
    4. LLM structured parsing (Groq)
    5. Validation
    """
    # Step 1: Validate
    validation = validate_image(image_bytes)
    if not validation['valid']:
        return {'success': False, 'error': validation['error']}

    # Step 2-3: OCR
    ocr_text = await extract_text_from_image(image_bytes)
    if not ocr_text:
        return {'success': False, 'error': 'Tidak bisa membaca teks dari gambar'}

    # Step 4-5: Parse + validate
    parsed = await parse_receipt_text(ocr_text)
    parsed['ocr_text_preview'] = ocr_text[:200]
    return parsed
