from __future__ import annotations


class TransientAiError(RuntimeError):
    """Временная ошибка внешнего AI-провайдера: нужно ретраить задачу/вебхук."""

    pass

