"""
Telegram Bot Handler — Command Router
======================================
Webhook-based Telegram bot for Personal Finance AI.
All commands route through here.
"""

import httpx
from datetime import date, timedelta
from typing import Optional

from app.config import settings


TELEGRAM_API = f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}"


async def send_message(chat_id: str, text: str, parse_mode: str = "Markdown"):
    """Send a Telegram message."""
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
        except Exception as e:
            print(f"[TG-ERR] Send failed: {e}")


async def get_file_url(file_id: str) -> Optional[str]:
    """Get download URL for a Telegram file (photo)."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{TELEGRAM_API}/getFile",
                params={"file_id": file_id},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok"):
                file_path = data["result"]["file_path"]
                return f"https://api.telegram.org/file/bot{settings.TELEGRAM_TOKEN}/{file_path}"
        except:
            pass
    return None


async def download_file(file_url: str) -> Optional[bytes]:
    """Download file content from Telegram."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(file_url, timeout=30)
            if resp.status_code == 200:
                return resp.content
        except:
            pass
    return None


async def handle_update(update: dict, db):
    """
    Main handler for Telegram webhook updates.
    Routes to appropriate command handlers.
    """
    from app.modules.auth.auth_service import (
        get_user_by_telegram_id, register_user, create_otp, audit_action
    )
    from app.modules.transactions.transaction_service import (
        create_transaction, get_transactions, get_summary, soft_delete_transaction,
        detect_anomalies
    )
    from app.modules.ai_processing.groq_rotator import groq_rotator
    from app.modules.ai_processing.ai_service import (
        ai_categorize, ai_financial_qa, get_conversation_memory, save_memory
    )

    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = message.get("text", "").strip()
    photo = message.get("photo")
    user_info = message.get("from", {})
    username = user_info.get("username", "")
    display_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()

    if not chat_id:
        return

    # --- Get or register user ---
    user = await get_user_by_telegram_id(db, chat_id)

    # --- /start command (registration) ---
    if text.startswith("/start"):
        if user:
            await send_message(chat_id,
                f"👋 Halo {display_name}! Kamu sudah terdaftar sebagai *{user.role.value}*.\n"
                f"Ketik /menu untuk lihat fitur.")
            return

        # Check for OTP in /start command: /start 123456
        parts = text.split()
        otp_code = parts[1] if len(parts) > 1 else None

        result = await register_user(db, chat_id, username, display_name, otp_code)
        if result['success']:
            role = result['role']
            await send_message(chat_id,
                f"✅ *Registrasi Berhasil!*\n\n"
                f"👤 Role: *{role.upper()}*\n"
                f"{'🔑 Kamu adalah Admin! Gunakan /approve untuk invite user baru.' if role == 'admin' else '🎉 Selamat datang!'}\n\n"
                f"Ketik /menu untuk mulai.")
        else:
            await send_message(chat_id,
                f"❌ Registrasi gagal: {result['error']}\n\n"
                f"Untuk bergabung, minta kode OTP dari admin lalu ketik:\n"
                f"`/start KODE_OTP`")
        return

    # --- Must be registered for other commands ---
    if not user:
        await send_message(chat_id,
            "⚠️ Kamu belum terdaftar.\n"
            "Ketik /start untuk mendaftar (admin)\n"
            "atau /start KODE\\_OTP untuk mendaftar via invite.")
        return

    user_id = user.id
    is_admin = user.role == "admin"
    t = text.lower()

    # --- /menu ---
    if t in ["/menu", "/help", "menu", "help"]:
        menu = (
            "🏦 *Personal Finance AI*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💰 *Transaksi:*\n"
            "  /tambah [jumlah] [keterangan]\n"
            "  /pemasukan [jumlah] [sumber]\n"
            "  /riwayat — 10 transaksi terakhir\n"
            "  /hapus [id] — Hapus transaksi\n"
            "  📸 Kirim foto struk — OCR otomatis\n\n"
            "📊 *Laporan:*\n"
            "  /hari — Ringkasan hari ini\n"
            "  /minggu — Ringkasan minggu ini\n"
            "  /bulan — Ringkasan bulan ini\n"
            "  /laporan — Laporan bulanan + AI insight\n"
            "  /anomali — Deteksi pengeluaran aneh\n\n"
            "📈 *Intelligence:*\n"
            "  /forecast — Prediksi cashflow 30 hari\n"
            "  /recurring — Deteksi langganan rutin\n"
            "  /export — Export ke Google Sheets\n\n"
            "🧠 *AI Assistant:*\n"
            "  Kirim pertanyaan apapun tentang keuanganmu\n"
            "  Contoh: \"Berapa total makan bulan ini?\"\n\n"
        )
        if is_admin:
            menu += (
                "🔑 *Admin:*\n"
                "  /approve — Generate kode invite\n"
                "  /users — Daftar pengguna\n"
                "  /health — System diagnostics\n"
            )
        await send_message(chat_id, menu)
        return

    # --- /approve (Admin only) ---
    if t == "/approve" and is_admin:
        code = await create_otp(db, user_id)
        await audit_action(db, user_id, "admin.create_otp")
        await send_message(chat_id,
            f"🔑 *Kode OTP untuk user baru:*\n\n"
            f"`{code}`\n\n"
            f"⏰ Berlaku {settings.OTP_EXPIRE_MINUTES} menit.\n"
            f"Kirim kode ini ke user baru. Mereka ketik:\n"
            f"`/start {code}`")
        return

    # --- /tambah (Add expense) ---
    if t.startswith("/tambah") or t.startswith("/keluar"):
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            await send_message(chat_id,
                "📝 Format: `/tambah 50000 makan siang`\n"
                "atau: `/tambah 25000`")
            return

        try:
            amount = float(parts[1].replace(",", "").replace(".", "").replace("k", "000"))
        except ValueError:
            await send_message(chat_id, "❌ Jumlah tidak valid. Contoh: `/tambah 50000`")
            return

        desc = parts[2] if len(parts) > 2 else ""

        # AI categorize
        category = "Lainnya"
        if desc:
            cat_result = await ai_categorize(desc)
            if cat_result:
                category = cat_result

        result = await create_transaction(
            db, user_id, "expense", amount, category, desc
        )
        if result['success']:
            await send_message(chat_id,
                f"✅ *Pengeluaran Dicatat*\n\n"
                f"💸 Rp {amount:,.0f}\n"
                f"📂 Kategori: {category}\n"
                f"📝 {desc if desc else '-'}\n"
                f"📅 {date.today().strftime('%d %b %Y')}")
        else:
            await send_message(chat_id, f"❌ {result['error']}")
        return

    # --- /pemasukan (Add income) ---
    if t.startswith("/pemasukan") or t.startswith("/masuk"):
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            await send_message(chat_id, "📝 Format: `/pemasukan 5000000 gaji`")
            return

        try:
            amount = float(parts[1].replace(",", "").replace(".", "").replace("k", "000"))
        except ValueError:
            await send_message(chat_id, "❌ Jumlah tidak valid.")
            return

        desc = parts[2] if len(parts) > 2 else "Pemasukan"
        result = await create_transaction(
            db, user_id, "income", amount, "Pemasukan", desc
        )
        if result['success']:
            await send_message(chat_id,
                f"✅ *Pemasukan Dicatat*\n\n"
                f"💰 Rp {amount:,.0f}\n"
                f"📝 {desc}")
        else:
            await send_message(chat_id, f"❌ {result['error']}")
        return

    # --- /riwayat (Transaction history) ---
    if t.startswith("/riwayat") or t.startswith("/history"):
        txs = await get_transactions(db, user_id, limit=10)
        if not txs:
            await send_message(chat_id, "📭 Belum ada transaksi.")
            return

        lines = ["📋 *10 Transaksi Terakhir*\n━━━━━━━━━━━━━━━━━━━━━━"]
        for tx in txs:
            emoji = "💰" if tx['type'] == 'income' else "💸"
            lines.append(
                f"{emoji} Rp {tx['amount']:,.0f} — {tx['category']}\n"
                f"   {tx['description'][:40] if tx['description'] else '-'} ({tx['date']})"
            )
        await send_message(chat_id, "\n".join(lines))
        return

    # --- /hari (Daily summary) ---
    if t in ["/hari", "/today", "/daily", "hari ini"]:
        today = date.today()
        summary = await get_summary(db, user_id, today, today)
        await send_message(chat_id,
            f"📊 *Ringkasan Hari Ini*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Pemasukan: Rp {summary['total_income']:,.0f}\n"
            f"💸 Pengeluaran: Rp {summary['total_expense']:,.0f}\n"
            f"📈 Net: Rp {summary['net']:,.0f}\n"
            f"📊 Transaksi: {summary['transaction_count']}\n"
            f"🏷️ Top: {summary['top_category']}")
        return

    # --- /minggu (Weekly summary) ---
    if t in ["/minggu", "/week", "/weekly"]:
        today = date.today()
        start = today - timedelta(days=today.weekday())
        summary = await get_summary(db, user_id, start, today)
        cats_str = "\n".join(
            f"  • {c['category']}: Rp {c['amount']:,.0f}"
            for c in summary['categories'][:5]
        )
        await send_message(chat_id,
            f"📊 *Ringkasan Minggu Ini*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Pemasukan: Rp {summary['total_income']:,.0f}\n"
            f"💸 Pengeluaran: Rp {summary['total_expense']:,.0f}\n"
            f"📈 Net: Rp {summary['net']:,.0f}\n"
            f"💹 Savings Rate: {summary['savings_rate']:.1f}%\n"
            f"📊 Avg/hari: Rp {summary['avg_daily_expense']:,.0f}\n\n"
            f"🏷️ *Per Kategori:*\n{cats_str}")
        return

    # --- /bulan (Monthly summary) ---
    if t in ["/bulan", "/month", "/monthly"]:
        today = date.today()
        start = today.replace(day=1)
        summary = await get_summary(db, user_id, start, today)
        cats_str = "\n".join(
            f"  • {c['category']}: Rp {c['amount']:,.0f}"
            for c in summary['categories'][:8]
        )
        await send_message(chat_id,
            f"📊 *Ringkasan Bulan Ini*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Pemasukan: Rp {summary['total_income']:,.0f}\n"
            f"💸 Pengeluaran: Rp {summary['total_expense']:,.0f}\n"
            f"📈 Net: Rp {summary['net']:,.0f}\n"
            f"💹 Savings Rate: {summary['savings_rate']:.1f}%\n"
            f"📊 Avg/hari: Rp {summary['avg_daily_expense']:,.0f}\n\n"
            f"🏷️ *Top Kategori:*\n{cats_str}")
        return

    # --- /anomali (Anomaly detection) ---
    if t in ["/anomali", "/anomaly"]:
        anomalies = await detect_anomalies(db, user_id)
        if not anomalies:
            await send_message(chat_id, "✅ Tidak ada anomali pengeluaran terdeteksi (30 hari).")
            return
        lines = ["⚠️ *Anomali Pengeluaran Terdeteksi*\n━━━━━━━━━━━━━━━━━━━━━━"]
        for a in anomalies[:5]:
            emoji = "🔴" if a['direction'] == 'HIGH' else "🔵"
            lines.append(
                f"{emoji} {a['date']}: Rp {a['amount']:,.0f}\n"
                f"   Z-score: {a['z_score']}, Avg: Rp {a['avg_daily']:,.0f}")
        await send_message(chat_id, "\n".join(lines))
        return

    # --- /health (Admin diagnostics) ---
    if t == "/health" and is_admin:
        api_status = groq_rotator.get_status()
        lines = [
            "🏥 *System Health*\n━━━━━━━━━━━━━━━━━━━━━━",
            f"🔑 API Keys: {api_status['total_keys']}",
            f"📍 Current Key: #{api_status['current_key']}",
        ]
        for k in api_status['keys']:
            icon = "🟢" if k['available'] else ("🟡" if k['in_cooldown'] else "🔴")
            lines.append(
                f"  {icon} Key #{k['key_index']}: "
                f"{k['total_requests']} req, {k['total_errors']} err")
        await send_message(chat_id, "\n".join(lines))
        return

    # --- /forecast ---
    if t in ["/forecast", "/prediksi"]:
        from app.modules.forecasting.forecast_service import forecast_cashflow
        fc = await forecast_cashflow(db, user_id, 30)
        await send_message(chat_id,
            f"📈 *Prediksi Cashflow 30 Hari*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Proyeksi Pemasukan: Rp {fc['projected_income']:,.0f}\n"
            f"💸 Proyeksi Pengeluaran: Rp {fc['projected_expense']:,.0f}\n"
            f"📊 Proyeksi Net: Rp {fc['projected_net']:,.0f}\n"
            f"💹 Savings Rate: {fc['savings_rate_projected']:.1f}%\n\n"
            f"📉 Pessimistic: Rp {fc['pessimistic_net']:,.0f}\n"
            f"📈 Optimistic: Rp {fc['optimistic_net']:,.0f}\n"
            f"📊 Data: {fc['data_days']} hari")
        return

    # --- /recurring ---
    if t in ["/recurring", "/langganan"]:
        from app.modules.forecasting.forecast_service import detect_recurring_expenses
        items = await detect_recurring_expenses(db, user_id)
        if not items:
            await send_message(chat_id, "📭 Belum ada pola pengeluaran rutin terdeteksi.")
            return
        lines = ["🔄 *Pengeluaran Rutin Terdeteksi*\n━━━━━━━━━━━━━━━━━━━━━━"]
        for item in items:
            lines.append(
                f"  • {item['description']}: Rp {item['amount']:,.0f}\n"
                f"    {item['category']} — {item['frequency']}x ({item['interval']})")
        await send_message(chat_id, "\n".join(lines))
        return

    # --- /laporan (Monthly report with AI) ---
    if t in ["/laporan", "/report"]:
        from app.modules.reporting.report_service import generate_monthly_report
        report = await generate_monthly_report(db, user_id)
        await send_message(chat_id, report)
        return

    # --- /export (Google Sheets) ---
    if t in ["/export", "/sheets"]:
        from app.modules.sheets_export.sheets_service import export_to_sheets
        today = date.today()
        month_start = today.replace(day=1)
        txs = await get_transactions(db, user_id, start_date=month_start, limit=500)
        summary = await get_summary(db, user_id, month_start, today)
        uname = user.username or user.display_name or 'User'
        result = await export_to_sheets(str(user_id), uname, txs, summary)
        if result['success']:
            await send_message(chat_id,
                f"✅ *Export Berhasil!*\n"
                f"📊 {result['rows_exported']} transaksi\n"
                f"📋 Sheet: {result['sheet_name']}\n"
                f"🔗 {result.get('spreadsheet_url', 'Link available in Google Drive')}")
        else:
            await send_message(chat_id, f"❌ Export gagal: {result['error']}")
        return

    # --- /backup (Admin only) ---
    if t == "/backup" and is_admin:
        from app.modules.backup.backup_service import backup_database
        result = await backup_database()
        if result['success']:
            await send_message(chat_id,
                f"✅ *Backup Berhasil*\n"
                f"📁 Method: {result['method']}\n"
                f"💾 Size: {result['size_kb']:.1f} KB")
        else:
            await send_message(chat_id, f"❌ Backup gagal: {result.get('error')}")
        return

    # --- Photo (OCR receipt) ---
    if photo:
        from app.modules.ocr.ocr_service import process_receipt
        await send_message(chat_id, "📸 _Memproses struk..._")
        # Get largest photo
        file_id = photo[-1]['file_id']
        file_url = await get_file_url(file_id)
        if not file_url:
            await send_message(chat_id, "❌ Gagal download foto.")
            return
        image_bytes = await download_file(file_url)
        if not image_bytes:
            await send_message(chat_id, "❌ Gagal download foto.")
            return
        parsed = await process_receipt(image_bytes)
        if parsed.get('success'):
            # Auto-save as expense
            result = await create_transaction(
                db, user_id, 'expense',
                parsed['total'], parsed.get('category', 'Belanja'),
                f"Struk: {parsed.get('merchant', 'Unknown')}",
                parsed.get('merchant'), parsed.get('date'),
                source='ocr'
            )
            await send_message(chat_id,
                f"✅ *Struk Berhasil Diproses!*\n\n"
                f"🏪 Merchant: {parsed.get('merchant', '-')}\n"
                f"💸 Total: Rp {parsed['total']:,.0f}\n"
                f"📂 Kategori: {parsed.get('category', '-')}\n"
                f"📅 Tanggal: {parsed.get('date', '-')}\n\n"
                f"{'✅ Tersimpan otomatis!' if result.get('success') else '⚠️ Gagal simpan: ' + result.get('error', '')}")
        else:
            await send_message(chat_id, f"❌ {parsed.get('error', 'Gagal proses struk')}")
        return

    # --- Default: AI Financial Q&A ---
    if text and not text.startswith("/"):
        await send_message(chat_id, "🧠 _Thinking..._")

        # Get user context
        today = date.today()
        month_start = today.replace(day=1)
        summary = await get_summary(db, user_id, month_start, today)

        # Conversation memory
        memory = await get_conversation_memory(db, user_id, limit=6)

        # Ask AI
        response = await ai_financial_qa(text, summary, memory)

        # Save memory
        await save_memory(db, user_id, "user", text)
        if response:
            await save_memory(db, user_id, "assistant", response)
            await send_message(chat_id, f"🧠 {response}")
        else:
            await send_message(chat_id, "❌ AI sedang tidak tersedia. Coba lagi nanti.")
        return
