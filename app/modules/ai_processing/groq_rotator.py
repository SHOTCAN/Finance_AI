"""
Groq API Key Rotation — 5-Key Load Balancer
============================================
- Priority cycling: Key1 → Key2 → ... → Key5 → repeat
- Per-key usage tracking
- Cooldown timer (60s) on rate-limited keys
- Health flags per key
- Pre-emptive switching before hitting limits
"""

import time
from groq import Groq
from app.config import settings


class GroqKeyRotator:
    """Manage 5 Groq API keys with intelligent rotation."""

    # Groq free tier: ~30 requests/min, ~14400/day per key
    REQUESTS_PER_MIN_LIMIT = 28  # Pre-emptive: switch at 28, before hitting 30
    COOLDOWN_SECONDS = 65  # Wait after rate limit

    def __init__(self):
        self._keys = settings.groq_keys
        self._current_idx = 0
        self._key_stats = {}
        for i, key in enumerate(self._keys):
            self._key_stats[i] = {
                'key': key,
                'requests_this_minute': 0,
                'requests_today': 0,
                'last_request': 0,
                'minute_start': 0,
                'cooldown_until': 0,
                'total_requests': 0,
                'total_errors': 0,
                'healthy': True,
                'last_error': None,
            }

    def _reset_minute_if_needed(self, idx: int):
        """Reset per-minute counter if a new minute has started."""
        stats = self._key_stats[idx]
        now = time.time()
        if now - stats['minute_start'] >= 60:
            stats['requests_this_minute'] = 0
            stats['minute_start'] = now

    def _is_available(self, idx: int) -> bool:
        """Check if a key is available (not in cooldown, not exhausted)."""
        stats = self._key_stats[idx]
        now = time.time()

        # In cooldown?
        if now < stats['cooldown_until']:
            return False

        # Pre-emptive: too many requests this minute?
        self._reset_minute_if_needed(idx)
        if stats['requests_this_minute'] >= self.REQUESTS_PER_MIN_LIMIT:
            return False

        return stats['healthy']

    def get_client(self) -> tuple:
        """
        Get next available Groq client.
        Returns (Groq client, key_index) or (None, -1) if all exhausted.
        """
        n = len(self._keys)
        if n == 0:
            return None, -1

        # Try from current index forward
        for offset in range(n):
            idx = (self._current_idx + offset) % n
            if self._is_available(idx):
                self._current_idx = idx
                return Groq(api_key=self._keys[idx]), idx

        # All exhausted — find the one with earliest cooldown end
        earliest_idx = 0
        earliest_time = float('inf')
        for idx in range(n):
            cd = self._key_stats[idx]['cooldown_until']
            if cd < earliest_time:
                earliest_time = cd
                earliest_idx = idx

        return Groq(api_key=self._keys[earliest_idx]), earliest_idx

    def record_success(self, idx: int):
        """Record a successful API call."""
        if idx < 0 or idx >= len(self._keys):
            return
        stats = self._key_stats[idx]
        stats['requests_this_minute'] += 1
        stats['requests_today'] += 1
        stats['total_requests'] += 1
        stats['last_request'] = time.time()
        stats['healthy'] = True

        # Pre-emptive switch: if approaching limit, advance to next
        if stats['requests_this_minute'] >= self.REQUESTS_PER_MIN_LIMIT:
            self._current_idx = (idx + 1) % len(self._keys)

    def record_error(self, idx: int, error_type: str = "rate_limit"):
        """Record an API error and apply cooldown."""
        if idx < 0 or idx >= len(self._keys):
            return
        stats = self._key_stats[idx]
        stats['total_errors'] += 1
        stats['last_error'] = error_type

        if error_type == "rate_limit":
            stats['cooldown_until'] = time.time() + self.COOLDOWN_SECONDS
            stats['healthy'] = False
        elif error_type == "auth_error":
            stats['healthy'] = False
            stats['cooldown_until'] = time.time() + 3600  # 1h cooldown for auth errors

        # Advance to next key
        self._current_idx = (idx + 1) % len(self._keys)

    def get_status(self) -> dict:
        """Get status of all keys (for diagnostics). Never expose key values."""
        status = []
        for idx, stats in self._key_stats.items():
            self._reset_minute_if_needed(idx)
            status.append({
                'key_index': idx + 1,
                'healthy': stats['healthy'],
                'requests_this_minute': stats['requests_this_minute'],
                'total_requests': stats['total_requests'],
                'total_errors': stats['total_errors'],
                'in_cooldown': time.time() < stats['cooldown_until'],
                'available': self._is_available(idx),
            })
        return {
            'total_keys': len(self._keys),
            'current_key': self._current_idx + 1,
            'keys': status,
        }

    async def chat(self, messages: list, model: str = "llama-3.3-70b-versatile",
                   temperature: float = 0.3, max_tokens: int = 1024) -> dict:
        """
        Send chat to Groq with automatic rotation on failure.
        Returns {success, content, key_used, usage}.
        """
        attempts = len(self._keys) + 1  # Try all keys + one retry

        for attempt in range(attempts):
            client, idx = self.get_client()
            if client is None:
                return {'success': False, 'error': 'No API keys configured'}

            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self.record_success(idx)
                return {
                    'success': True,
                    'content': response.choices[0].message.content,
                    'key_used': idx + 1,
                    'usage': {
                        'prompt_tokens': response.usage.prompt_tokens,
                        'completion_tokens': response.usage.completion_tokens,
                    },
                }
            except Exception as e:
                err_str = str(e).lower()
                if 'rate_limit' in err_str or '429' in err_str:
                    self.record_error(idx, "rate_limit")
                elif 'authentication' in err_str or '401' in err_str:
                    self.record_error(idx, "auth_error")
                else:
                    self.record_error(idx, f"unknown: {type(e).__name__}")

                if attempt >= attempts - 1:
                    print(f"[Groq Rotator] All keys exhausted/failed. Last error: {e}")
                    return {'success': False, 'error': 'Layanan AI sedang dalam maintenance. Silakan coba beberapa saat lagi.', 'key_tried': idx + 1}

        print("[Groq Rotator] All keys exhausted.")
        return {'success': False, 'error': 'Layanan AI sedang dalam maintenance. Silakan coba beberapa saat lagi.'}


# Singleton
groq_rotator = GroqKeyRotator()
