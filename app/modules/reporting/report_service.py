"""
Reporting Module — Automated Reports + AI Narratives
=====================================================
- Daily/weekly/monthly report generation
- Deterministic calculations (from transaction_service)
- AI narrative summaries via Groq (reasoning only)
- Background task scheduling ready
"""

from datetime import date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.transactions.transaction_service import get_summary, get_transactions
from app.modules.ai_processing.groq_rotator import groq_rotator


async def generate_daily_report(db: AsyncSession, user_id) -> str:
    """Generate daily report text for Telegram."""
    today = date.today()
    summary = await get_summary(db, user_id, today, today)

    report = (
        f"📅 *Laporan Harian — {today.strftime('%d %b %Y')}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Pemasukan: Rp {summary['total_income']:,.0f}\n"
        f"💸 Pengeluaran: Rp {summary['total_expense']:,.0f}\n"
        f"📈 Net: Rp {summary['net']:,.0f}\n"
        f"📊 Transaksi: {summary['transaction_count']}\n"
    )

    if summary['categories']:
        report += "\n🏷️ *Pengeluaran per Kategori:*\n"
        for c in summary['categories'][:5]:
            pct = (c['amount'] / max(summary['total_expense'], 1)) * 100
            report += f"  • {c['category']}: Rp {c['amount']:,.0f} ({pct:.0f}%)\n"

    return report


async def generate_weekly_report(db: AsyncSession, user_id) -> str:
    """Generate weekly report with trend comparison."""
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_start - timedelta(days=1)

    current = await get_summary(db, user_id, week_start, today)
    previous = await get_summary(db, user_id, prev_week_start, prev_week_end)

    # Trend comparison (deterministic)
    expense_change = 0
    if previous['total_expense'] > 0:
        expense_change = ((current['total_expense'] - previous['total_expense'])
                         / previous['total_expense'] * 100)

    trend_emoji = "📈" if expense_change > 5 else ("📉" if expense_change < -5 else "➡️")

    report = (
        f"📊 *Laporan Mingguan*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {week_start.strftime('%d %b')} — {today.strftime('%d %b %Y')}\n\n"
        f"💰 Pemasukan: Rp {current['total_income']:,.0f}\n"
        f"💸 Pengeluaran: Rp {current['total_expense']:,.0f}\n"
        f"📈 Net: Rp {current['net']:,.0f}\n"
        f"💹 Savings Rate: {current['savings_rate']:.1f}%\n"
        f"📊 Avg/hari: Rp {current['avg_daily_expense']:,.0f}\n\n"
        f"{trend_emoji} *vs Minggu Lalu:*\n"
        f"  Pengeluaran: {expense_change:+.1f}%\n"
    )

    if current['categories']:
        report += "\n🏷️ *Top Kategori:*\n"
        for c in current['categories'][:5]:
            report += f"  • {c['category']}: Rp {c['amount']:,.0f}\n"

    return report


async def generate_monthly_report(db: AsyncSession, user_id) -> str:
    """Generate monthly report with AI narrative summary."""
    today = date.today()
    month_start = today.replace(day=1)

    summary = await get_summary(db, user_id, month_start, today)

    report = (
        f"📊 *Laporan Bulanan — {today.strftime('%B %Y')}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Total Pemasukan: Rp {summary['total_income']:,.0f}\n"
        f"💸 Total Pengeluaran: Rp {summary['total_expense']:,.0f}\n"
        f"📈 Net: Rp {summary['net']:,.0f}\n"
        f"💹 Savings Rate: {summary['savings_rate']:.1f}%\n"
        f"📊 Avg/hari: Rp {summary['avg_daily_expense']:,.0f}\n"
        f"📝 Total Transaksi: {summary['transaction_count']}\n\n"
    )

    if summary['categories']:
        report += "🏷️ *Breakdown Pengeluaran:*\n"
        for c in summary['categories']:
            pct = (c['amount'] / max(summary['total_expense'], 1)) * 100
            bar_len = int(pct / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            report += f"  {c['category']}: {bar} {pct:.0f}%\n"
            report += f"    Rp {c['amount']:,.0f}\n"

    # AI narrative summary (reasoning only)
    ai_narrative = await _generate_ai_narrative(summary)
    if ai_narrative:
        report += f"\n🧠 *AI Insight:*\n{ai_narrative}"

    return report


async def _generate_ai_narrative(summary: dict) -> str:
    """LLM generates narrative insight from deterministic data."""
    messages = [
        {
            "role": "system",
            "content": (
                "Kamu adalah financial advisor AI. Berikan 2-3 kalimat insight singkat "
                "tentang kondisi keuangan user berdasarkan data yang diberikan. "
                "Fokus pada: savings rate, pola pengeluaran, dan saran actionable. "
                "Gunakan Bahasa Indonesia santai. Jangan ulangi angka yang sudah ada di laporan."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Data bulan ini:\n"
                f"- Income: Rp {summary['total_income']:,.0f}\n"
                f"- Expense: Rp {summary['total_expense']:,.0f}\n"
                f"- Savings rate: {summary['savings_rate']:.1f}%\n"
                f"- Top category: {summary['top_category']}\n"
                f"- Avg daily expense: Rp {summary['avg_daily_expense']:,.0f}"
            ),
        },
    ]

    result = await groq_rotator.chat(messages, max_tokens=200, temperature=0.5)
    return result.get('content') if result.get('success') else None
