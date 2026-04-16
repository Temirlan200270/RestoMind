• Backend

  🔴 Критичность: High Backend WhatsApp dedupe ломает retry и теряет сообщения
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/api/webhooks.py:636, /C:/Users/Gulmira/Desktop/RestoMind/app/api/
  webhooks.py:706, /C:/Users/Gulmira/Desktop/RestoMind/app/services/whatsapp_idempotency.py:18
  Угроза: process_with_retry() повторяет process_message() с тем же whatsapp_message_id. Первая попытка на строках 708-
  714 сразу пишет message_id в dedupe и коммитит. Если дальше упадет OpenAI/БД/WhatsApp, вторая попытка увидит дубль и
  вернется как успешная. Итог: ARQ/FastAPI считают сообщение обработанным, FailedTask не создается, заказ/чат теряются.
  Как исправить: dedupe должен иметь состояния processing/done/failed, а не “вставил значит обработано”. Минимальный
  паттерн:

  # app/db/models.py
  class WhatsappInboundDedupe(Base):
      __tablename__ = "whatsapp_inbound_dedupe"

      message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
      phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
      status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="processing")
      attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
      error: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
      created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
      processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

  # app/services/whatsapp_idempotency.py
  async def try_start_whatsapp_message(db: AsyncSession, *, message_id: str, phone: str) -> bool:
      mid = (message_id or "").strip()
      if not mid:
          return True

      stmt = (
          pg_insert(WhatsappInboundDedupe)
          .values(message_id=mid, phone=phone, status="processing", attempts=1)
          .on_conflict_do_update(
              index_elements=["message_id"],
              set_={
                  "attempts": WhatsappInboundDedupe.attempts + 1,
                  "status": "processing",
                  "error": "",
              },
              where=WhatsappInboundDedupe.status != "done",
          )
      )
      res = await db.execute(stmt)
      return (res.rowcount or 0) == 1


  async def mark_whatsapp_done(db: AsyncSession, message_id: str) -> None:
      await db.execute(
          update(WhatsappInboundDedupe)
          .where(WhatsappInboundDedupe.message_id == message_id)
          .values(status="done", processed_at=func.now(), error="")
      )


  async def mark_whatsapp_failed(db: AsyncSession, message_id: str, error: str) -> None:
      await db.execute(
          update(WhatsappInboundDedupe)
          .where(WhatsappInboundDedupe.message_id == message_id)
          .values(status="failed", error=error[:2000])
      )

  # app/api/webhooks.py
  async def process_with_retry(...):
      org_id = ...
      if whatsapp_message_id:
          async with async_session_factory() as db:
              can_process = await try_start_whatsapp_message(
                  db, message_id=whatsapp_message_id, phone=phone
              )
              await db.commit()
          if not can_process:
              return

      last_exc = None
      for attempt in range(MAX_RETRIES):
          try:
              await process_message(..., whatsapp_message_id="")
              if whatsapp_message_id:
                  async with async_session_factory() as db:
                      await mark_whatsapp_done(db, whatsapp_message_id)
                      await db.commit()
              return
          except Exception as exc:
              last_exc = exc
              ...

      if whatsapp_message_id:
          async with async_session_factory() as db:
              await mark_whatsapp_failed(db, whatsapp_message_id, str(last_exc))
              await db.commit()

  🔴 Критичность: High Backend Внешний OpenAI вызывается внутри открытой DB-сессии
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/api/webhooks.py:912, /C:/Users/Gulmira/Desktop/RestoMind/app/api/
  webhooks.py:958, /C:/Users/Gulmira/Desktop/RestoMind/app/api/webhooks.py:1053
  Угроза: на строке 912 открывается AsyncSession, затем в этой же сессии выполняются SELECT’ы и идет await
  call_openai(...). Под нагрузкой медленный OpenAI держит соединение/транзакцию PostgreSQL до строки 1053. При 20-30
  параллельных вебхуках пул pool_size=20, max_overflow=10 будет забит ожиданием внешнего API, а не работой БД. Это дает
  очередь запросов, таймауты и лавину повторных webhook delivery.
  Как исправить: разделить фазы “прочитать контекст”, “вызвать OpenAI”, “коротко записать результат”:

  # 1. DB только для чтения контекста
  async with async_session_factory() as db:
      menu_items = await load_available_menu(db, organization_id=organization_id)
      u_row = await db.scalar(
          select(User).where(User.phone == phone, User.organization_id == organization_id)
      )
      customer_ctx = await build_customer_context(db, u_row)
      kb_context = await load_knowledge_context_block(db, organization_id)
      draft_row = await get_open_draft_order(db, phone, organization_id)
      draft_ctx = format_draft_order_context_for_prompt(
          draft_row.items_json if draft_row else None
      )

  # 2. Внешний API без удержания DB connection
  ai_response = await call_openai(
      history,
      message_text,
      build_menu_context(menu_items),
      kb_context,
      draft_order_context=draft_ctx,
      sales_strategy_context=strategy_ctx,
      customer_context=customer_ctx,
  )

  # 3. DB только для мутации
  async with async_session_factory() as db:
      result = await route_intent(
          db,
          phone,
          ai_response,
          menu_items=menu_items,
          organization_id=organization_id,
          inbound_message_id=wmid,
      )
      outbound_id_chat = await _save_chat_log(...)
      await db.commit()

  🔴 Критичность: High Backend Отправка в iiko не защищена от двойного клика/двух операторов
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/api/admin.py:702, /C:/Users/Gulmira/Desktop/RestoMind/app/api/
  admin.py:766, /C:/Users/Gulmira/Desktop/RestoMind/app/api/admin.py:789
  Угроза: PATCH /orders/{id} не принимает expected_version, не делает атомарный UPDATE ... WHERE status='confirmed', и
  вызывает _send_order_to_iiko() до фиксации нового состояния в БД. Два параллельных запроса видят confirmed, оба
  вызывают iiko, оба могут напечатать заказ на кухне. Это прямые потери денег и операционный хаос.
  Как исправить: сначала атомарно перевести заказ в промежуточное состояние sending_to_iiko или поставить lock, затем
  отправлять. Для начала достаточно optimistic update:

  # schema
  class OrderPatchBody(BaseModel):
      status: str
      expected_version: int | None = None

  # endpoint
  if cur == OrderStatus.CONFIRMED.value and want == OrderStatus.SENT_TO_IIKO.value:
      expected = body.expected_version if body.expected_version is not None else int(order.row_version)

      claimed = (
          await db.execute(
              update(Order)
              .where(
                  Order.id == order.id,
                  Order.organization_id == org_id,
                  Order.status == OrderStatus.CONFIRMED.value,
                  Order.row_version == expected,
              )
              .values(
                  status="sending_to_iiko",
                  row_version=Order.row_version + 1,
                  iiko_last_error=None,
              )
          )
      ).rowcount
      if claimed != 1:
          raise HTTPException(status_code=409, detail="Заказ уже изменен или отправляется в iiko")

      await db.commit()

      sent, err, raw = await _send_order_to_iiko(...)

      async with async_session_factory() as db2:
          locked = await db2.get(Order, order.id)
          if sent:
              locked.status = OrderStatus.SENT_TO_IIKO.value
              locked.iiko_last_error = None
              locked.row_version = int(locked.row_version) + 1
          else:
              locked.status = OrderStatus.CONFIRMED.value
              locked.iiko_last_error = err or "iiko: неизвестная ошибка"
              locked.row_version = int(locked.row_version) + 1
          await db2.commit()

  🔴 Критичность: High Backend Payment webhook idempotency не атомарна
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/services/payment_webhook.py:53, /C:/Users/Gulmira/Desktop/RestoMind/app/
  db/models.py:643
  Угроза: проверка select(PaymentEvent.id) и последующий db.add(PaymentEvent(...)) не защищены unique constraint’ом. Два
  одинаковых webhook’а одновременно оба увидят “нет события”, оба вставят webhook_paid, оба могут поставить оплату и
  отправить клиенту уведомление.
  Как исправить: добавить уникальность и использовать insert-on-conflict:

  # app/db/models.py
  class PaymentEvent(Base):
      __tablename__ = "payment_events"
      __table_args__ = (
          UniqueConstraint("order_id", "event_type", "note", name="uq_payment_event_idempotency"),
      )

  # app/services/payment_webhook.py
  stmt = (
      pg_insert(PaymentEvent)
      .values(
          order_id=order.id,
          event_type="webhook_paid",
          actor="webhook",
          amount=amt,
          note=note_key,
      )
      .on_conflict_do_nothing(
          index_elements=["order_id", "event_type", "note"],
      )
  )
  res = await db.execute(stmt)
  if (res.rowcount or 0) == 0:
      return {"ok": True, "duplicate": True, "prepayment_status": order.prepayment_status}

  order.prepayment_status = "paid"
  order.payment_provider = prov
  order.external_payment_id = ext_id
  order.payment_amount_captured = amt

  🔴 Критичность: High Backend Уведомление об оплате ставится в очередь до commit
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/api/payment_webhook.py:77, /C:/Users/Gulmira/Desktop/RestoMind/app/api/
  payment_webhook.py:83, /C:/Users/Gulmira/Desktop/RestoMind/app/db/session.py:50
  Угроза: get_db() коммитит после возврата endpoint’а. Но _run_payment_webhook() ставит payment_notify_customer в ARQ/
  background на строках 83-87 до commit. Worker может прочитать старый prepayment_status, а при ошибке commit клиент все
  равно получит уведомление об оплате.
  Как исправить: в webhook endpoint делать явный commit до enqueue:

  async def _run_payment_webhook(...):
      ...
      should_notify = (
          out.get("ok")
          and not out.get("duplicate")
          and body.status == "paid"
          and (out.get("prepayment_status") or "").strip().lower() == "paid"
      )

      await db.commit()

      if should_notify:
          ok = await enqueue_job("payment_notify_customer", order_id=int(body.order_id))
          if not ok:
              background_tasks.add_task(run_payment_received_customer_notify, body.order_id)

      return out

  🔴 Критичность: Medium Backend OpenAI failures превращаются в “успешную” обработку, ARQ не retry’ит
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/services/ai_brain.py:225, /C:/Users/Gulmira/Desktop/RestoMind/app/
  services/ai_brain.py:245, /C:/Users/Gulmira/Desktop/RestoMind/app/api/webhooks.py:973
  Угроза: после всех ошибок OpenAI call_openai() возвращает fallback intent="escalate", не бросает исключение. Для ARQ
  это успешная задача. Временный сбой OpenAI превращается в эскалацию оператору, хотя retry очереди мог бы обработать
  сообщение через минуту. При массовом rate limit операторский поток будет завален.
  Как исправить: разделить transient и deterministic fallback:

  class TransientAiError(RuntimeError):
      pass

  async def call_openai(..., raise_on_transient: bool = True) -> A:


  # внутри route_intent или сразу перед commit
  user.current_state = result.new_state.value if result.new_state else user.current_state
  user.current_pending_order_id = result.pending_order_id

  await db.commit()

  # после commit только кеш
  if result.new_state:
      await redis_client.set(state_key, result.new_state.value, ex=STATE_TTL)

  🔴 Критичность: Low Backend Дублирование sales strategy даст разные допродажи в разных ветках
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/services/sales_strategy.py:53, /C:/Users/Gulmira/Desktop/RestoMind/app/
  services/strategy_engine.py:28
  Угроза: две реализации _cart_iiko_ids, _rejected_iiko_ids/_rejected_iiko, _offered... расходятся по правилам. В проде
  это означает: бот может повторно предлагать уже отклоненную позицию или аналитика/синхронизация рекомендаций будет
  считать другое состояние, чем диалог.
  Как исправить: оставить один модуль доменной логики, например app/services/upsell_state.py:

  def cart_iiko_ids(items: list[dict[str, Any]]) -> set[str]:
      return {
          str(it.get("iiko_id") or "").strip().lower()
          for it in items
          if str(it.get("iiko_id") or "").strip()
      }

  def rejected_upsell_iiko_ids(meta: dict[str, Any]) -> set[str]:
      raw = meta.get("upsell_rejected_iiko_ids") or []
      return {str(x).strip().lower() for x in raw if str(x).strip()}

  def offered_upsell_iiko_ids(meta: dict[str, Any]) -> set[str]:
      ...

  Frontend

  🔴 Критичность: High Frontend REST/WS гонка перетирает свежие данные заказа
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/static/js/admin-app.js:2746, /C:/Users/Gulmira/Desktop/RestoMind/app/
  static/js/admin-app.js:2881, /C:/Users/Gulmira/Desktop/RestoMind/app/static/js/admin-app.js:3522
  Угроза: ws_ready запускает loadOrders(), а onOrderUpdated() без проверки версии делает splice(..., data.order). Любой
  старый REST-ответ может присвоить this.orders = data.orders || [] после более свежего WS-события. Оператор увидит
  старый статус/row_version, отправит неверный PATCH или повторит iiko.
  Как исправить: хранить монотонный номер загрузки и не применять старые ответы; при WS применять только если
  row_version не старее локальной:

  async loadOrders() {
      const reqId = ++this._ordersLoadSeq;
      const { ok, status, data } = await this.apiJsonResponse(`/api/admin/orders?${p.toString()}`);
      if (reqId !== this._ordersLoadSeq) return;
      if (!ok) {
          this.ordersLoadError = this.formatApiError(data.detail) || `Не удалось загрузить заказы (${status})`;
          return;
      }

      const incoming = data.orders || [];
      const byId = new Map(this.orders.map((o) => [Number(o.id), o]));
      this.orders = incoming.map((next) => {
          const prev = byId.get(Number(next.id));
          if (prev && Number(prev.row_version || 0) > Number(next.row_version || 0)) {
              return prev;
          }
          return next;
      });
  }

  onOrderUpdated(data) {
      if (!data.order) {
          void this.loadOrders();
          return;
      }
      const oid = Number(data.order.id);
      const idx = this.orders.findIndex((o) => Number(o.id) === oid);
      const prev = idx >= 0 ? this.orders[idx] : null;

      if (prev && Number(prev.row_version || 0) > Number(data.order.row_version || 0)) {
          return;
      }

      if (idx >= 0) this.orders.splice(idx, 1, data.order);
      else this.orders.unshift(data.order);
  }

  🔴 Критичность: High Frontend Смена статуса заказа не отправляет expected_version
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/static/js/admin-app.js:1331, /C:/Users/Gulmira/Desktop/RestoMind/app/api/
  admin.py:702
  Угроза: rebuild/payment split используют expected_version, а самый денежный сценарий confirmed → sent_to_iiko нет. UI
  может отправить устаревший статус после WS/REST гонки, а backend сейчас тоже это принимает.
  Как исправить: передавать версию и на backend проверять ее:

  async patchOrderStatus(orderId, status) {
      const order = this.orders.find((o) => Number(o.id) === Number(orderId));
      const { ok, data } = await this.apiJsonResponse(`/api/admin/orders/${orderId}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
              status,
              expected_version: order?.row_version ?? null,
          }),
      });
      ...
  }

  if body.expected_version is not None and int(order.row_version) != int(body.expected_version):
      raise HTTPException(status_code=409, detail="Заказ изменился. Обновите список.")

  🔴 Критичность: Medium Frontend Ошибки загрузки заказов уходят только в console.warn
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/static/js/admin-app.js:3536, /C:/Users/Gulmira/Desktop/RestoMind/app/
  static/js/admin-app.js:3552, [app/static/js/admin-app.js](/C:/ его в шаб:

  async loadOrders() {
      this.ordersLoadError = '';
      const { ok, status, data } = await this.apiJsonResponse(`/api/admin/orders?${p.toString()}`);
      if (!ok) {
          this.ordersLoadError = this.formatApiError(data.detail) || `Не удалось загрузить заказы (${status})`;
          void this.showUiAlert(this.ordersLoadError, 'Ошибка');
          return;
      }
      this.orders = data.orders || [];
  }

  <div x-show="ordersLoadError" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
      <span x-text="ordersLoadError"></span>
  </div>

  🔴 Критичность: Medium Frontend Reconnect timers WebSocket могут накапливаться
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/static/js/admin-app.js:2704, /C:/Users/Gulmira/Desktop/RestoMind/app/
  static/js/admin-app.js:2726
  Угроза: scheduleReconnect() создает setTimeout, но id не хранится и не очищается при ручном connectWebSocket(),
  logout/login или смене токена. После нестабильной сети несколько таймеров могут параллельно открывать сокеты. Код
  закрывает старый сокет, но события/коннекты будут дергаться лишний раз, что усиливает WS/REST гонки.
  Как исправить:

  scheduleReconnect() {
      if (this._wsReconnectTimer) clearTimeout(this._wsReconnectTimer);
      this._wsReconnectTimer = setTimeout(() => {
          this._wsReconnectTimer = null;
          if (!this.authenticated || !this.wsToken) return;
          this.wsEpoch++;
          this.wsReconnectDelay = Math.min(this.wsReconnectDelay * 1.5, 15000);
          this.connectWebSocket();
      }, this.wsReconnectDelay);
  },

  disconnectWebSocket() {
      if (this._wsReconnectTimer) {
          clearTimeout(this._wsReconnectTimer);
          this._wsReconnectTimer = null;
      }
      this._clearWsReadyTimer();
      if (this.ws) {
          this.ws.onopen = this.ws.onclose = this.ws.onerror = this.ws.onmessage = null;
          this.ws.close();
          this.ws = null;
      }
      this.wsChannelReady = false;
  }

  🔴 Критичность: Low Frontend Дублирование карточек заказов повышает риск разных действий на desktop/mobile
  Где: /C:/Users/Gulmira/Desktop/RestoMind/app/templates/admin.html:823, /C:/Users/Gulmira/Desktop/RestoMind/app/
  templates/admin.html:871, /C:/Users/Gulmira/Desktop/RestoMind/app/templates/admin.html:919, /C:/Users/Gulmira/Desktop/
  RestoMind/app/templates/admin.html:959, /C:/Users/Gulmira/Desktop/RestoMind/app/templates/admin.html:1030
  Угроза: iiko error badge, payment/prepayment fields, row_version-dependent buttons и клиентские данные повторяются в
  kanban/table/mobile ветках. При следующей правке одно место легко забыть, и мобильный оператор будет принимать решение
  по неполному статусу.
  Как исправить: вынести повторяемый order summary в Jinja macro:

  {# app/templates/admin/_order_card.html #}
  {% macro order_status_bits(order_expr) %}
  <div x-show="{{ order_expr }}.iiko_last_error" class="mt-2 rounded-lg border border-red-300 bg-red-50 px-2 py-2">
      <p class="text-[10px] font-bold uppercase text-red-900">Ошибка iiko</p>
      <p class="text-xs text-red-900" x-text="{{ order_expr }}.iiko_last_error"></p>
  </div>
  <div class="font-mono text-xs text-gray-700" x-text="{{ order_expr }}.user_phone || '—'"></div>
  {% from "admin/_order_card.html" import order_status_bits %}
  {{ order_status_bits("order") }}

  static/js/admin-app.js:3615, /C:/Users/Gulmira/Desktop/RestoMind/app/api/admin.py:872
  Угроза: две JS-функции отправляют один и тотКак исправить: оставить одну функцию
  submitOrderRebuild({ closeComposition }) и использовать ее из обеих кнопок:

  async submitOrderRebuild({ closeComposition = false } = {}) {
      ...
      if (updated) {
          this.selectedOrder = updated;
          this.initOrderRebuildFromSelected();
          this.initOrderCompositionLinesFromSelected();
          this.syncOrderPaymentFormFromSelected();
          if (closeComposition) this.orderCompositionOpen = false;
      }
  }

Исправь всё это, но перед этим пройдись по плану проекта и просмотри другие файлы чтобы ничего не сломать 