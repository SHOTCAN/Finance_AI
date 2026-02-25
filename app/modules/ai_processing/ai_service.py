"""
AI Service — Categorization + Financial Q&A
============================================
LLM used ONLY for:
  - Reasoning / categorization
  - Narrative summaries
  - Contextual Q&A
All numeric calculations are deterministic (in transaction_service).
"""

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AIMemory
from app.modules.ai_processing.groq_rotator import groq_rotator

# Indonesian expense categories
CATEGORIES = [
    "Makanan", "Minuman", "Transportasi", "Belanja", "Hiburan",
    "Kesehatan", "Pendidikan", "Tagihan", "Pulsa & Internet",
    "Rumah Tangga", "Pakaian", "Donasi", "Investasi",
    "Asuransi", "Perawatan Diri", "Lainnya",
]


async def ai_categorize(description: str) -> str:
    """
    Use LLM to categorize a transaction description.
    Falls back to 'Lainnya' if AI unavailable.
    """
    if not description or not description.strip():
        return "Lainnya"

    messages = [
        {
            "role": "system",
            "content": (
                "Kamu adalah asisten kategori keuangan. "
                "Dari deskripsi transaksi, tentukan 1 kategori yang paling tepat.\n"
                f"Kategori yang tersedia: {', '.join(CATEGORIES)}\n"
                "Jawab HANYA dengan nama kategori, tanpa penjelasan."
            ),
        },
        {"role": "user", "content": description},
    ]

    result = await groq_rotator.chat(messages, max_tokens=20, temperature=0.1)

    if result['success']:
        category = result['content'].strip().strip('"').strip("'")
        # Validate against known categories
        for cat in CATEGORIES:
            if cat.lower() == category.lower():
                return cat
        # Fuzzy match
        for cat in CATEGORIES:
            if cat.lower() in category.lower() or category.lower() in cat.lower():
                return cat
        return "Lainnya"

    return "Lainnya"


async def ai_financial_qa(question: str, summary: dict,
                          memory: list = None) -> str:
    """
    AI financial advisor with context from user's actual data.
    LLM for reasoning and narrative only; data is deterministic.
    """
    context = (
        f"Data keuangan user bulan ini:\n"
        f"- Total pemasukan: Rp {summary['total_income']:,.0f}\n"
        f"- Total pengeluaran: Rp {summary['total_expense']:,.0f}\n"
        f"- Net: Rp {summary['net']:,.0f}\n"
        f"- Savings rate: {summary['savings_rate']:.1f}%\n"
        f"- Rata-rata pengeluaran/hari: Rp {summary['avg_daily_expense']:,.0f}\n"
        f"- Jumlah transaksi: {summary['transaction_count']}\n"
    )
    if summary.get('categories'):
        cats = ", ".join(
            f"{c['category']} (Rp {c['amount']:,.0f})"
            for c in summary['categories'][:5]
        )
        context += f"- Top kategori: {cats}\n"

    messages = [
        {
            "role": "system",
            "content": (
                "Kamu adalah AI asisten keuangan personal yang cerdas dan helpful. "
                "Jawab dalam Bahasa Indonesia yang santai tapi informatif.\n"
                "ATURAN PENTING:\n"
                "1. Gunakan data keuangan yang diberikan sebagai fakta — jangan mengarang angka.\n"
                "2. Berikan insight, saran, dan analisis berdasarkan data.\n"
                "3. Jika ditanya tentang data yang tidak tersedia, sampaikan keterbatasannya.\n"
                "4. Jaga privasi — jangan sebutkan identitas user.\n"
                "5. Jawab singkat dan to-the-point (max 200 kata).\n\n"
                f"KONTEKS DATA:\n{context}"
            ),
        },
    ]

    # Add conversation memory for context
    if memory:
        for mem in memory:
            messages.append({"role": mem['role'], "content": mem['content']})

    messages.append({"role": "user", "content": question})

    result = await groq_rotator.chat(messages, max_tokens=500, temperature=0.4)

    if result['success']:
        return result['content']
    return None


# ============================================
# CONVERSATION MEMORY (per user)
# ============================================

async def get_conversation_memory(db: AsyncSession, user_id, limit: int = 6) -> list:
    """Get recent conversation history for context (row-level isolated)."""
    result = await db.execute(
        select(AIMemory)
        .where(AIMemory.user_id == user_id)
        .order_by(desc(AIMemory.created_at))
        .limit(limit)
    )
    memories = result.scalars().all()
    # Reverse to chronological order
    return [
        {'role': m.role, 'content': m.content[:500]}  # Truncate long messages
        for m in reversed(memories)
    ]


async def save_memory(db: AsyncSession, user_id, role: str, content: str):
    """Save a conversation turn to memory."""
    db.add(AIMemory(
        user_id=user_id,
        role=role,
        content=content[:1000],  # Max 1000 chars per message
    ))

    # Cleanup: keep only last 50 messages per user
    all_mem = await db.execute(
        select(AIMemory)
        .where(AIMemory.user_id == user_id)
        .order_by(desc(AIMemory.created_at))
    )
    memories = all_mem.scalars().all()
    if len(memories) > 50:
        for old in memories[50:]:
            await db.delete(old)
