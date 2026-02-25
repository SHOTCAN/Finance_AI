"""
Google Sheets Export — Per-User Report Mirror
==============================================
- Each user gets their own Google Sheet (via service account)
- Structured export: monthly transactions, summaries
- Used as report mirror, NOT source of truth (PostgreSQL is)
"""

from datetime import date
from typing import Optional

from app.config import settings


async def export_to_sheets(user_id: str, username: str,
                           transactions: list, summary: dict) -> dict:
    """
    Export user's monthly data to Google Sheets.
    Creates/updates a sheet per user.
    """
    if not settings.GOOGLE_SHEETS_ENABLED:
        return {'success': False, 'error': 'Google Sheets disabled'}

    try:
        import gspread
        from google.oauth2 import service_account

        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive',
        ]
        credentials = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=scopes,
        )
        client = gspread.authorize(credentials)

        sheet_title = f"Finance AI — {username}"
        today = date.today()
        month_name = today.strftime('%B %Y')

        # Open or create spreadsheet
        try:
            spreadsheet = client.open(sheet_title)
        except gspread.SpreadsheetNotFound:
            spreadsheet = client.create(sheet_title)
            # Share with service account
            spreadsheet.share(
                settings.GOOGLE_SERVICE_ACCOUNT_FILE.replace('.json', ''),
                perm_type='user',
                role='writer',
                notify=False,
            )

        # Get or create monthly worksheet
        try:
            worksheet = spreadsheet.worksheet(month_name)
            worksheet.clear()
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=month_name, rows=200, cols=10)

        # Header row
        headers = ['Tanggal', 'Tipe', 'Jumlah', 'Kategori', 'Keterangan', 'Sumber']
        worksheet.update(range_name='A1:F1', values=[headers])

        # Transaction rows
        rows = []
        for tx in transactions:
            rows.append([
                tx.get('date', ''),
                tx.get('type', ''),
                tx.get('amount', 0),
                tx.get('category', ''),
                tx.get('description', '')[:50],
                tx.get('source', ''),
            ])

        if rows:
            end_row = len(rows) + 1
            worksheet.update(range_name=f'A2:F{end_row}', values=rows)

        # Summary section
        summary_start = len(rows) + 3
        summary_data = [
            ['', '', '', '', '', ''],
            ['=== RINGKASAN ===', '', '', '', '', ''],
            ['Total Pemasukan', summary.get('total_income', 0)],
            ['Total Pengeluaran', summary.get('total_expense', 0)],
            ['Net', summary.get('net', 0)],
            ['Savings Rate', f"{summary.get('savings_rate', 0):.1f}%"],
            ['Total Transaksi', summary.get('transaction_count', 0)],
        ]
        worksheet.update(
            range_name=f'A{summary_start}:F{summary_start + len(summary_data)}',
            values=summary_data
        )

        return {
            'success': True,
            'spreadsheet_url': spreadsheet.url,
            'sheet_name': month_name,
            'rows_exported': len(rows),
        }

    except ImportError:
        return {'success': False, 'error': 'gspread not installed'}
    except Exception as e:
        return {'success': False, 'error': f'Sheets export failed: {e}'}
