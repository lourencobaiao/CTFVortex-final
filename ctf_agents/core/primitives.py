"""Communication primitives for multi-agent CTF scenarios."""

import threading
from typing import Any, Callable, Dict, List, Optional


class SharedState:
    """Thread-safe dictionary wrapper for sharing state between agents.

    Supports get/set/delete with locking, plus a snapshot method
    that returns a shallow copy of the current state.
    """

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def snapshot(self) -> Dict[str, Any]:
        """Return a shallow copy of the current state."""
        with self._lock:
            return dict(self._data)

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class EventBus:
    """Simple publish/subscribe event bus.

    Subscribers register callbacks for event names. Publishing an event
    calls all registered callbacks synchronously in registration order.
    Callback exceptions are caught and printed so that one failing
    subscriber does not prevent others from running.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()

    def subscribe(self, event: str, callback: Callable) -> None:
        """Register a callback for an event name."""
        with self._lock:
            self._subscribers.setdefault(event, []).append(callback)

    def unsubscribe(self, event: str, callback: Callable) -> None:
        """Remove a specific callback from an event."""
        with self._lock:
            if event in self._subscribers:
                self._subscribers[event] = [
                    cb for cb in self._subscribers[event] if cb is not callback
                ]

    def publish(self, event: str, **kwargs) -> None:
        """Fire all callbacks for an event. kwargs are passed to each callback."""
        with self._lock:
            callbacks = list(self._subscribers.get(event, []))
        for cb in callbacks:
            try:
                cb(**kwargs)
            except Exception as exc:
                print(f"EventBus: callback {cb!r} for event {event!r} "
                      f"raised {exc!r}")

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscribers.clear()
