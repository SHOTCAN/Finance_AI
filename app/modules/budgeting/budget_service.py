"""
Budgeting Module — Monthly Spending Rules
==========================================
Handles setting category budgets and checking limits.
"""

from datetime import date
from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Budget, Transaction, TransactionType, TransactionSource, AuditLog


async def set_budget(db: AsyncSession, user_id: UUID, category: str, monthly_limit: float) -> dict:
    """Set or update a monthly budget limit for a specific category."""
    if monthly_limit < 0:
        return {'success': False, 'error': 'Limit tidak boleh negatif'}
        
    # Check if budget already exists for this category and user
    result = await db.execute(
        select(Budget).where(
            Budget.user_id == user_id,
            func.lower(Budget.category) == category.lower()
        )
    )
    budget = result.scalar_one_or_none()
    
    if budget:
        budget.monthly_limit = monthly_limit
        budget.is_active = (monthly_limit > 0)
    else:
        if monthly_limit > 0:
            budget = Budget(
                user_id=user_id,
                category=category,
                monthly_limit=monthly_limit
            )
            db.add(budget)
            
    # Audit log
    db.add(AuditLog(
        user_id=user_id,
        action="budget.set",
        details={"category": category, "monthly_limit": monthly_limit},
    ))
            
    return {
        'success': True,
        'message': f'Budget kategori *{category}* berhasil diatur ke Rp {monthly_limit:,.0f}/bulan.'
    }


async def get_budgets(db: AsyncSession, user_id: UUID) -> List[dict]:
    """Get all active budgets for a user and calculate current usage."""
    result = await db.execute(
        select(Budget).where(
            Budget.user_id == user_id,
            Budget.is_active == True
        ).order_by(Budget.category)
    )
    budgets = result.scalars().all()
    
    out = []
    # Calculate spending for each category this month
    today = date.today()
    start_of_month = today.replace(day=1)
    
    for b in budgets:
        spend_result = await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                Transaction.user_id == user_id,
                func.lower(Transaction.category) == b.category.lower(),
                Transaction.type == TransactionType.EXPENSE,
                Transaction.is_deleted == False,
                Transaction.transaction_date >= start_of_month,
                Transaction.transaction_date <= today,
            )
        )
        spent = float(spend_result.scalar())
        
        out.append({
            'category': b.category,
            'limit': b.monthly_limit,
            'spent': spent,
            'remaining': b.monthly_limit - spent,
            'usage_percent': (spent / b.monthly_limit * 100) if b.monthly_limit > 0 else 0
        })
        
    return out


async def check_budget_status(db: AsyncSession, user_id: UUID, category: str, amount: float) -> dict:
    """
    Check if a new transaction will exceed the budget limit.
    Returns success if no limit or under limit.
    Returns error if it exceeds the limit.
    """
    # 1. Find if there is an active budget for this category
    result = await db.execute(
        select(Budget).where(
            Budget.user_id == user_id,
            func.lower(Budget.category) == category.lower(),
            Budget.is_active == True
        )
    )
    budget = result.scalar_one_or_none()
    
    if not budget:
        return {'success': True} # No budget set, anything goes
        
    # 2. Check current spending
    today = date.today()
    start_of_month = today.replace(day=1)
    
    spend_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.user_id == user_id,
            func.lower(Transaction.category) == category.lower(),
            Transaction.type == TransactionType.EXPENSE,
            Transaction.is_deleted == False,
            Transaction.transaction_date >= start_of_month,
            Transaction.transaction_date <= today,
        )
    )
    spent = float(spend_result.scalar())
    
    # 3. Predict new spending
    projected = spent + amount
    
    if projected > budget.monthly_limit:
        return {
            'success': False,
            'code': 'BUDGET_EXCEEDED',
            'limit': budget.monthly_limit,
            'spent': spent,
            'remaining': budget.monthly_limit - spent,
            'projected': projected
        }
        
    return {'success': True}
