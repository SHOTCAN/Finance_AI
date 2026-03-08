"""
Transaction Module — CRUD with Soft-Delete
===========================================
- Row-level isolation (all queries filtered by user_id)
- Soft-delete (is_deleted flag, never hard delete)
- Idempotent transaction handling (idempotency_key)
- Duplicate detection via amount+date+description hash
- Deterministic budget/savings calculations (no LLM)
"""

import hashlib
import statistics
from datetime import date, datetime, timedelta
from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, func, and_, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Transaction, TransactionType, TransactionSource, Budget, AuditLog


# ============================================
# TRANSACTION CRUD
# ============================================

def _generate_idempotency_key(user_id: str, amount: float, description: str,
                               tx_date: date) -> str:
    """Generate deterministic key for duplicate detection."""
    raw = f"{user_id}|{amount}|{description}|{tx_date.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def create_transaction(
    db: AsyncSession,
    user_id: UUID,
    tx_type: str,
    amount: float,
    category: str = "Lainnya",
    description: str = "",
    merchant: str = None,
    tx_date: date = None,
    source: str = "manual",
    idempotency_key: str = None,
    is_emergency: bool = False,
) -> dict:
    """Create a new transaction with duplicate protection."""
    if amount <= 0:
        return {'success': False, 'error': 'Jumlah harus > 0'}

    tx_date = tx_date or date.today()

    # Generate idempotency key if not provided
    if not idempotency_key:
        idempotency_key = _generate_idempotency_key(
            str(user_id), amount, description, tx_date
        )

    # Check duplicate
    existing = await db.execute(
        select(Transaction).where(
            Transaction.idempotency_key == idempotency_key,
            Transaction.is_deleted == False
        )
    )
    if existing.scalar_one_or_none():
        return {'success': False, 'error': 'Transaksi duplikat terdeteksi'}

    # Check budget for expenses
    if tx_type == "expense" and not is_emergency:
        from app.modules.budgeting.budget_service import check_budget_status
        b_status = await check_budget_status(db, user_id, category, amount)
        if not b_status['success'] and b_status.get('code') == 'BUDGET_EXCEEDED':
            return b_status

    tx = Transaction(
        user_id=user_id,
        type=TransactionType(tx_type),
        amount=amount,
        category=category,
        description=description,
        merchant=merchant,
        transaction_date=tx_date,
        source=TransactionSource(source),
        idempotency_key=idempotency_key,
    )
    db.add(tx)

    # Audit
    db.add(AuditLog(
        user_id=user_id,
        action=f"transaction.create.{tx_type}",
        details={"amount": amount, "category": category},
    ))

    await db.flush()
    return {
        'success': True,
        'transaction_id': str(tx.id),
        'type': tx_type,
        'amount': amount,
        'category': category,
    }


async def soft_delete_transaction(db: AsyncSession, user_id: UUID, tx_id: UUID) -> dict:
    """Soft-delete a transaction (mark is_deleted=True)."""
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == tx_id,
            Transaction.user_id == user_id,  # Row-level isolation
            Transaction.is_deleted == False,
        )
    )
    tx = result.scalar_one_or_none()
    if not tx:
        return {'success': False, 'error': 'Transaksi tidak ditemukan'}

    tx.is_deleted = True
    tx.updated_at = datetime.utcnow()

    db.add(AuditLog(
        user_id=user_id,
        action="transaction.delete",
        details={"transaction_id": str(tx_id), "amount": tx.amount},
    ))

    return {'success': True, 'message': 'Transaksi dihapus'}


async def get_transactions(
    db: AsyncSession,
    user_id: UUID,
    start_date: date = None,
    end_date: date = None,
    tx_type: str = None,
    category: str = None,
    limit: int = 50,
) -> List[dict]:
    """Get transactions for a user (row-level isolation, excludes soft-deleted)."""
    query = select(Transaction).where(
        Transaction.user_id == user_id,
        Transaction.is_deleted == False,
    ).order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc())

    if start_date:
        query = query.where(Transaction.transaction_date >= start_date)
    if end_date:
        query = query.where(Transaction.transaction_date <= end_date)
    if tx_type:
        query = query.where(Transaction.type == TransactionType(tx_type))
    if category:
        query = query.where(Transaction.category == category)

    query = query.limit(limit)

    result = await db.execute(query)
    txs = result.scalars().all()

    return [
        {
            'id': str(tx.id),
            'type': tx.type.value,
            'amount': tx.amount,
            'category': tx.category,
            'description': tx.description,
            'merchant': tx.merchant,
            'date': tx.transaction_date.isoformat(),
            'source': tx.source.value,
        }
        for tx in txs
    ]


# ============================================
# DETERMINISTIC FINANCIAL CALCULATIONS
# ============================================

async def get_summary(db: AsyncSession, user_id: UUID,
                      start_date: date, end_date: date) -> dict:
    """Deterministic financial summary — no LLM, pure math."""

    # Total income
    income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.INCOME,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
        )
    )
    total_income = float(income_result.scalar())

    # Total expense
    expense_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
        )
    )
    total_expense = float(expense_result.scalar())

    # Expense by category
    cat_result = await db.execute(
        select(Transaction.category, func.sum(Transaction.amount)).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
        ).group_by(Transaction.category).order_by(func.sum(Transaction.amount).desc())
    )
    categories = [{'category': row[0], 'amount': float(row[1])} for row in cat_result]

    # Transaction count
    count_result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user_id,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
        )
    )
    tx_count = int(count_result.scalar())

    # Derived metrics (deterministic, no LLM)
    net = total_income - total_expense
    savings_rate = (net / total_income * 100) if total_income > 0 else 0
    avg_daily_expense = total_expense / max((end_date - start_date).days, 1)

    return {
        'period': f"{start_date.isoformat()} → {end_date.isoformat()}",
        'total_income': round(total_income, 0),
        'total_expense': round(total_expense, 0),
        'net': round(net, 0),
        'savings_rate': round(savings_rate, 1),
        'avg_daily_expense': round(avg_daily_expense, 0),
        'transaction_count': tx_count,
        'categories': categories,
        'top_category': categories[0]['category'] if categories else 'N/A',
    }


async def detect_anomalies(db: AsyncSession, user_id: UUID,
                           lookback_days: int = 30, z_threshold: float = 2.0) -> list:
    """
    Z-score based anomaly detection on daily spending.
    Pure statistical, no LLM judgment.
    """
    end = date.today()
    start = end - timedelta(days=lookback_days)

    # Daily expense totals
    daily_result = await db.execute(
        select(
            Transaction.transaction_date,
            func.sum(Transaction.amount)
        ).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
        ).group_by(Transaction.transaction_date)
        .order_by(Transaction.transaction_date)
    )
    daily_totals = [(row[0], float(row[1])) for row in daily_result]

    if len(daily_totals) < 7:
        return []

    amounts = [d[1] for d in daily_totals]
    mean_val = statistics.mean(amounts)
    std_val = statistics.stdev(amounts) if len(amounts) > 1 else 1

    anomalies = []
    for dt, amount in daily_totals:
        if std_val > 0:
            z = (amount - mean_val) / std_val
            if abs(z) > z_threshold:
                anomalies.append({
                    'date': dt.isoformat(),
                    'amount': round(amount, 0),
                    'z_score': round(z, 2),
                    'direction': 'HIGH' if z > 0 else 'LOW',
                    'avg_daily': round(mean_val, 0),
                    'deviation': round(abs(amount - mean_val), 0),
                })

    return anomalies

async def delete_transaction(db: AsyncSession, user_id: UUID, short_id: str) -> dict:
    """Soft-delete a transaction using a short 8-char ID."""
    try:
        from sqlalchemy import cast, String
        
        result = await db.execute(
            select(Transaction)
            .where(
                and_(
                    Transaction.user_id == user_id,
                    Transaction.is_deleted == False,
                    cast(Transaction.id, String).like(f"{short_id}%")
                )
            )
        )
        tx = result.scalar_one_or_none()
        
        if not tx:
            return {'success': False, 'error': 'Transaksi tidak ditemukan atau sudah dihapus.'}
            
        tx.is_deleted = True
        
        try:
            from app.database import AuditLog
            log = AuditLog(
                user_id=user_id,
                action="transaction.delete",
                details={"tx_id": str(tx.id), "amount": tx.amount, "category": tx.category}
            )
            db.add(log)
        except Exception:
            pass
            
        return {
            'success': True, 
            'message': f"Transaksi Rp {tx.amount:,.0f} ({tx.category}) berhasil dihapus."
        }
    except Exception as e:
        print(f"[Transaction] Delete failed: {e}")
        return {'success': False, 'error': 'Gagal menghapus transaksi.'}

async def reset_user_finances(db: AsyncSession, user_id: UUID) -> dict:
    """Soft-delete ALL transactions and purge AI memory for this user."""
    try:
        from app.database import AIMemory, AuditLog
        
        # Get all transactions
        result = await db.execute(
            select(Transaction).where(
                and_(Transaction.user_id == user_id, Transaction.is_deleted == False)
            )
        )
        transactions = result.scalars().all()
        
        count = 0
        for tx in transactions:
            tx.is_deleted = True
            count += 1
                
        # Hard purge memory to reset AI context
        await db.execute(
            AIMemory.__table__.delete().where(AIMemory.user_id == user_id)
        )
        
        # Log reset
        try:
            log = AuditLog(
                user_id=user_id,
                action="user.reset_finance",
                details={"deleted_count": count}
            )
            db.add(log)
        except Exception:
            pass
            
        return {'success': True, 'count': count}
    except Exception as e:
        print(f"[System] Reset failed: {e}")
        return {'success': False, 'error': 'Layanan Reset Finansial gagal diproses.'}
