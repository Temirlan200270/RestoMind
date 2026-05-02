"""
Клиент iiko Cloud API.
Реализует авторизацию и получение номенклатуры (меню).
Все вызовы — асинхронные через httpx.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import httpx

logger = logging.getLogger(__name__)

IIKO_BASE_URL = "https://api-ru.iiko.services"
REQUEST_TIMEOUT = 15.0
MAX_RETRIES = 2
RETRY_DELAY = 1.0


class IikoClient:
    """
    Асинхронный клиент для iiko Cloud API.

    Использование:
        async with IikoClient(api_login="...") as client:
            menu = await client.get_nomenclature(org_id="...")
    """

    def __init__(self, api_login: str) -> None:
        self._api_login = api_login
        self._token: str | None = None
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "IikoClient":
        self._http = httpx.AsyncClient(
            base_url=IIKO_BASE_URL,
            timeout=REQUEST_TIMEOUT,
        )
        await self._authenticate()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def _ensure_token(self) -> None:
        """
        Гарантирует, что токен есть. Это не "фикс бага сейчас", а страховка
        на случай, если клиент начнут использовать долго (токен живёт ~15 минут).
        """
        if not self._token:
            await self._authenticate()

    async def _authenticate(self) -> None:
        """
        Получить токен доступа iiko.
        Токен живёт ~15 минут, после чего нужно запросить новый.
        """
        if not self._http:
            raise RuntimeError("HTTP-клиент не инициализирован. Используйте async with.")

        response = await self._http.post(
            "/api/1/access_token",
            json={"apiLogin": self._api_login},
        )
        response.raise_for_status()
        data = response.json()

        self._token = data.get("token")
        if not self._token:
            raise ValueError("iiko API не вернул токен авторизации")

        logger.info("iiko: авторизация успешна")

    def _auth_headers(self) -> dict[str, str]:
        """Заголовки с токеном для авторизованных запросов."""
        if not self._token:
            raise RuntimeError("Токен не получен. Сначала вызовите _authenticate().")
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        timeout: float | None = None,
        retry_on_4xx: bool = False,
        retry_transient: bool = True,
    ) -> dict[str, Any]:
        """
        Единая точка политики HTTP-вызовов:
        - retry на сетевые ошибки и 5xx
        - обработка 401: переавторизация + 1 повтор
        - единые логи тела ошибки для диагностики

        Возвращает JSON-ответ iiko как dict.

        ``retry_transient=False`` — для мутаций вроде deliveries/create: повтор после таймаута
        может создать второй заказ в iiko, пока первый уже ушёл на сервер.
        """
        if not self._http:
            raise RuntimeError("HTTP-клиент не инициализирован.")

        await self._ensure_token()

        last_exc: Exception | None = None
        refreshed = False
        max_attempts = MAX_RETRIES if retry_transient else 1

        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._http.request(
                    method=method,
                    url=path,
                    headers=self._auth_headers(),
                    json=dict(json) if json is not None else None,
                    timeout=timeout,
                )

                if response.status_code == 401 and not refreshed:
                    # Токен протух: переавторизация и один повтор в рамках того же attempt.
                    logger.warning("iiko: 401 на %s %s — переавторизация", method, path)
                    await self._authenticate()
                    refreshed = True
                    response = await self._http.request(
                        method=method,
                        url=path,
                        headers=self._auth_headers(),
                        json=dict(json) if json is not None else None,
                        timeout=timeout,
                    )

                if response.status_code >= 400:
                    body_preview = (response.text or "")[:2000]
                    logger.error(
                        "iiko: %s %s HTTP %s (попытка %d/%d). body=%s",
                        method,
                        path,
                        response.status_code,
                        attempt,
                        max_attempts,
                        body_preview,
                    )

                response.raise_for_status()

                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("iiko API вернул неожиданный JSON (ожидали object)")
                return data

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                logger.warning(
                    "iiko: сеть при %s %s (попытка %d/%d): %s",
                    method,
                    path,
                    attempt,
                    max_attempts,
                    exc,
                )
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else 0
                if status >= 500:
                    logger.warning(
                        "iiko: 5xx при %s %s (попытка %d/%d): %s",
                        method,
                        path,
                        attempt,
                        max_attempts,
                        status,
                    )
                elif retry_on_4xx and status >= 400:
                    logger.warning(
                        "iiko: 4xx при %s %s (попытка %d/%d): %s",
                        method,
                        path,
                        attempt,
                        max_attempts,
                        status,
                    )
                else:
                    raise

            if attempt < max_attempts:
                await asyncio.sleep(RETRY_DELAY * attempt)

        raise last_exc or RuntimeError(f"iiko: не удалось выполнить {method} {path}")

    async def get_organizations(self) -> list[dict[str, Any]]:
        """Получить список организаций, привязанных к API-логину."""
        data = await self._request("POST", "/api/1/organizations", json={})
        orgs = data.get("organizations", [])
        logger.info("iiko: найдено %d организаций", len(orgs))
        return orgs

    async def get_nomenclature(self, organization_id: str) -> dict[str, Any]:
        """
        Получить полную номенклатуру (меню) организации.

        Returns:
            Словарь с ключами 'groups' (категории) и 'products' (позиции).
        """
        # Крупная номенклатура может отвечать дольше стандартного таймаута
        data = await self._request(
            "POST",
            "/api/1/nomenclature",
            json={"organizationId": organization_id},
            timeout=60.0,
        )

        groups = data.get("groups", [])
        products = data.get("products", [])
        logger.info(
            "iiko: загружено %d категорий, %d продуктов",
            len(groups), len(products),
        )
        return data

    async def fetch_menu(self, organization_id: str) -> dict[str, Any]:
        """
        Синоним ``get_nomenclature`` (ТЗ): дерево групп и продуктов для синхронизации ``menu_items``.
        """
        return await self.get_nomenclature(organization_id)

    async def create_delivery_order(
        self,
        organization_id: str,
        order_data: dict[str, Any],
        *,
        terminal_group_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Создать заказ на доставку/самовывоз в iiko.
        Эндпоинт: POST /api/1/deliveries/create
        Включает retry при сетевых сбоях.

        Args:
            organization_id: UUID организации в iiko.
            order_data: Словарь с данными заказа (корзина, комментарий и т.д.).
            terminal_group_id: UUID группы терминалов (касса/кухня) — часто обязателен для печати на нужной точке.

        Returns:
            Ответ iiko с orderInfo (correlationId, orderId и т.д.).
        """
        payload: dict[str, Any] = {
            "organizationId": organization_id,
            "order": order_data,
        }
        tg = (terminal_group_id or "").strip()
        if tg:
            payload["terminalGroupId"] = tg

        try:
            order_obj = payload.get("order") if isinstance(payload, dict) else None
            customer_obj = order_obj.get("customer") if isinstance(order_obj, dict) else None
            phone = customer_obj.get("phone") if isinstance(customer_obj, dict) else None
            ph = str(phone or "").strip()
            tail = ph[-4:] if len(ph) >= 4 else ("****" if ph else "")
            logger.info("iiko: deliveries/create order_id_hint=%s phone_tail=%s", order_data.get("externalNumber"), tail)
        except Exception:
            pass

        result = await self._request(
            "POST",
            "/api/1/deliveries/create",
            json=payload,
            retry_transient=False,
        )
        correlation_id = result.get("correlationId", "?")
        logger.info("iiko: заказ создан, correlationId=%s", correlation_id)
        return result

    async def get_stop_lists(self, organization_ids: list[str]) -> dict[str, Any]:
        """
        Получить стоп-листы (позиции, которых нет в наличии).
        Эндпоинт: POST /api/1/stop_lists

        Args:
            organization_ids: Список UUID организаций.

        Returns:
            Словарь со стоп-листами по организациям.
        """
        data = await self._request("POST", "/api/1/stop_lists", json={"organizationIds": organization_ids})
        logger.info("iiko: получены стоп-листы для %d организаций", len(organization_ids))
        return data

    async def get_terminal_groups(
        self,
        organization_ids: list[str],
        *,
        include_disabled: bool = False,
    ) -> dict[str, Any]:
        """
        Список терминальных групп (кассы/точки) по организациям.
        Эндпоинт: POST /api/1/terminal_groups
        """
        data = await self._request(
            "POST",
            "/api/1/terminal_groups",
            json={
                "organizationIds": organization_ids,
                "includeDisabled": include_disabled,
            },
        )
        logger.info("iiko: запрошены терминальные группы для %d организаций", len(organization_ids))
        return data

    async def get_delivery_order_types(self, organization_ids: list[str]) -> dict[str, Any]:
        """
        Типы заказов для deliveries/create (orderTypeId / orderServiceType).
        Эндпоинт: POST /api/1/deliveries/order_types

        Args:
            organization_ids: Список UUID организаций.

        Returns:
            Словарь со списками типов заказов (структура зависит от iiko-окружения).
        """
        data = await self._request("POST", "/api/1/deliveries/order_types", json={"organizationIds": organization_ids})
        logger.info("iiko: запрошены типы заказов deliveries для %d организаций", len(organization_ids))
        return data
