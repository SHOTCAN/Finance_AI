import asyncio
from app.modules.ocr.ocr_service import extract_and_parse_receipt

async def main():
    # Load the actual reference image provided by user
    with open(r'C:\Users\baldy\.gemini\antigravity\brain\bcdc7ed1-6c72-49c2-82ff-59132bed273a\media__1770469209179.jpg', 'rb') as f:
        img_bytes = f.read()
    
    res = await extract_and_parse_receipt(img_bytes)
    print("\nResult:")
    print(res)

asyncio.run(main())
