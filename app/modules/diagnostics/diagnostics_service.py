"""
Diagnostics Module — Self-Monitoring
=====================================
- System health checks
- Groq API key status
- Database connectivity
- Performance metrics
"""

import time
import psutil
from datetime import datetime

from app.modules.ai_processing.groq_rotator import groq_rotator


async def get_system_diagnostics(db=None) -> dict:
    """Full system health report."""
    diagnostics = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'status': 'healthy',
        'checks': {},
    }

    # 1. Groq API status
    api_status = groq_rotator.get_status()
    available_keys = sum(1 for k in api_status['keys'] if k['available'])
    diagnostics['checks']['groq_api'] = {
        'total_keys': api_status['total_keys'],
        'available': available_keys,
        'current_key': api_status['current_key'],
        'healthy': available_keys > 0,
    }

    # 2. Database connectivity
    if db:
        try:
            from sqlalchemy import text
            await db.execute(text("SELECT 1"))
            diagnostics['checks']['database'] = {'healthy': True}
        except Exception as e:
            diagnostics['checks']['database'] = {'healthy': False, 'error': str(e)}
            diagnostics['status'] = 'degraded'
    else:
        diagnostics['checks']['database'] = {'healthy': True, 'note': 'not tested'}

    # 3. System resources
    try:
        diagnostics['checks']['system'] = {
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent if hasattr(psutil, 'disk_usage') else 0,
        }
    except Exception:
        diagnostics['checks']['system'] = {'note': 'psutil not available'}

    # Overall status
    unhealthy = sum(
        1 for c in diagnostics['checks'].values()
        if isinstance(c, dict) and c.get('healthy') is False
    )
    if unhealthy > 0:
        diagnostics['status'] = 'degraded'

    return diagnostics
