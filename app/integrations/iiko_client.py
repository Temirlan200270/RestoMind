"""
Клиент iiko Cloud API.
Реализует авторизацию и получение номенклатуры (меню).
Все вызовы — асинхронные через httpx.
"""

import asyncio
import logging
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

    async def get_organizations(self) -> list[dict[str, Any]]:
        """Получить список организаций, привязанных к API-логину."""
        if not self._http:
            raise RuntimeError("HTTP-клиент не инициализирован.")

        response = await self._http.post(
            "/api/1/organizations",
            headers=self._auth_headers(),
            json={},
        )
        response.raise_for_status()
        data = response.json()
        orgs = data.get("organizations", [])
        logger.info("iiko: найдено %d организаций", len(orgs))
        return orgs

    async def get_nomenclature(self, organization_id: str) -> dict[str, Any]:
        """
        Получить полную номенклатуру (меню) организации.

        Returns:
            Словарь с ключами 'groups' (категории) и 'products' (позиции).
        """
        if not self._http:
            raise RuntimeError("HTTP-клиент не инициализирован.")

        response = await self._http.post(
            "/api/1/nomenclature",
            headers=self._auth_headers(),
            json={"organizationId": organization_id},
        )
        response.raise_for_status()
        data = response.json()

        groups = data.get("groups", [])
        products = data.get("products", [])
        logger.info(
            "iiko: загружено %d категорий, %d продуктов",
            len(groups), len(products),
        )
        return data

    async def create_delivery_order(
        self,
        organization_id: str,
        order_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Создать заказ на доставку/самовывоз в iiko.
        Эндпоинт: POST /api/1/deliveries/create
        Включает retry при сетевых сбоях.

        Args:
            organization_id: UUID организации в iiko.
            order_data: Словарь с данными заказа.

        Returns:
            Ответ iiko с orderInfo (correlationId, orderId и т.д.).
        """
        if not self._http:
            raise RuntimeError("HTTP-клиент не инициализирован.")

        payload = {
            "organizationId": organization_id,
            "order": order_data,
        }

        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._http.post(
                    "/api/1/deliveries/create",
                    headers=self._auth_headers(),
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()

                correlation_id = result.get("correlationId", "?")
                logger.info("iiko: заказ создан, correlationId=%s", correlation_id)
                return result

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                logger.warning(
                    "iiko: сетевая ошибка при создании заказа (попытка %d/%d): %s",
                    attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY * attempt)

        raise last_exc or RuntimeError("iiko: не удалось создать заказ")

    async def get_stop_lists(self, organization_ids: list[str]) -> dict[str, Any]:
        """
        Получить стоп-листы (позиции, которых нет в наличии).
        Эндпоинт: POST /api/1/stop_lists

        Args:
            organization_ids: Список UUID организаций.

        Returns:
            Словарь со стоп-листами по организациям.
        """
        if not self._http:
            raise RuntimeError("HTTP-клиент не инициализирован.")

        response = await self._http.post(
            "/api/1/stop_lists",
            headers=self._auth_headers(),
            json={"organizationIds": organization_ids},
        )
        response.raise_for_status()
        data = response.json()

        logger.info("iiko: получены стоп-листы для %d организаций", len(organization_ids))
        return data
