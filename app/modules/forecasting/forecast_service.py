"""
Forecasting Module — Deterministic Cashflow Prediction
======================================================
All calculations pure math — NO LLM for numeric logic.
- 30/60/90 day cashflow forecast
- Recurring expense detection
- Emergency fund calculator
- Savings rate projection
"""

import statistics
from datetime import date, timedelta
from collections import Counter
from typing import List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Transaction, TransactionType


async def forecast_cashflow(db: AsyncSession, user_id,
                            days_forward: int = 30) -> dict:
    """
    Predict future cashflow based on historical patterns.
    Pure deterministic — rolling averages, no ML.
    """
    today = date.today()
    lookback = timedelta(days=90)
    start = today - lookback

    # Historical daily income
    income_result = await db.execute(
        select(
            Transaction.transaction_date,
            func.sum(Transaction.amount)
        ).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.INCOME,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start,
        ).group_by(Transaction.transaction_date)
    )
    daily_income = {row[0]: float(row[1]) for row in income_result}

    # Historical daily expense
    expense_result = await db.execute(
        select(
            Transaction.transaction_date,
            func.sum(Transaction.amount)
        ).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start,
        ).group_by(Transaction.transaction_date)
    )
    daily_expense = {row[0]: float(row[1]) for row in expense_result}

    # Calculate averages
    total_days = max((today - start).days, 1)
    avg_daily_income = sum(daily_income.values()) / total_days
    avg_daily_expense = sum(daily_expense.values()) / total_days
    avg_daily_net = avg_daily_income - avg_daily_expense

    # Standard deviation for uncertainty range
    income_vals = list(daily_income.values()) if daily_income else [0]
    expense_vals = list(daily_expense.values()) if daily_expense else [0]
    income_std = statistics.stdev(income_vals) if len(income_vals) > 1 else 0
    expense_std = statistics.stdev(expense_vals) if len(expense_vals) > 1 else 0

    # Forecast
    projected_income = avg_daily_income * days_forward
    projected_expense = avg_daily_expense * days_forward
    projected_net = projected_income - projected_expense

    # Pessimistic / Optimistic scenarios (1 std dev)
    pessimistic_net = (avg_daily_income - income_std) * days_forward - \
                      (avg_daily_expense + expense_std) * days_forward
    optimistic_net = (avg_daily_income + income_std) * days_forward - \
                     (avg_daily_expense - expense_std) * days_forward

    return {
        'period_days': days_forward,
        'projected_income': round(projected_income, 0),
        'projected_expense': round(projected_expense, 0),
        'projected_net': round(projected_net, 0),
        'pessimistic_net': round(pessimistic_net, 0),
        'optimistic_net': round(optimistic_net, 0),
        'avg_daily_income': round(avg_daily_income, 0),
        'avg_daily_expense': round(avg_daily_expense, 0),
        'data_days': total_days,
        'savings_rate_projected': round(
            (projected_net / max(projected_income, 1)) * 100, 1
        ),
    }


async def detect_recurring_expenses(db: AsyncSession, user_id,
                                     months: int = 3) -> List[dict]:
    """
    Detect recurring expenses (subscriptions, bills).
    Uses description + amount frequency matching.
    """
    start = date.today() - timedelta(days=months * 30)

    result = await db.execute(
        select(
            Transaction.description,
            Transaction.category,
            Transaction.amount,
            func.count(Transaction.id).label('frequency'),
        ).where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start,
        ).group_by(
            Transaction.description,
            Transaction.category,
            Transaction.amount,
        ).having(func.count(Transaction.id) >= 2)
        .order_by(func.count(Transaction.id).desc())
    )

    recurring = []
    for row in result:
        desc, category, amount, freq = row
        if not desc:
            continue
        # Estimate frequency pattern
        interval = "bulanan" if freq >= months else "tidak teratur"
        recurring.append({
            'description': desc,
            'category': category,
            'amount': float(amount),
            'frequency': freq,
            'interval': interval,
            'monthly_cost': round(float(amount) * freq / months, 0),
        })

    return recurring[:10]  # Top 10


def calculate_emergency_fund(monthly_expense: float, months_coverage: int = 6) -> dict:
    """
    Emergency fund calculator.
    Standard recommendation: 3-6 months of expenses.
    """
    target_3m = monthly_expense * 3
    target_6m = monthly_expense * 6
    target_custom = monthly_expense * months_coverage

    return {
        'monthly_expense': round(monthly_expense, 0),
        'target_3_months': round(target_3m, 0),
        'target_6_months': round(target_6m, 0),
        f'target_{months_coverage}_months': round(target_custom, 0),
        'recommendation': (
            f"Dana darurat ideal: Rp {target_6m:,.0f} (6 bulan pengeluaran). "
            f"Minimum: Rp {target_3m:,.0f} (3 bulan)."
        ),
    }
