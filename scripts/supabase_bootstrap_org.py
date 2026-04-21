import asyncio
import os
import ssl

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is required")

    ctx = ssl.create_default_context()
    # Local Windows env иногда не имеет нужных CA для Supabase pooler.
    # Это утилита для аварийного бутстрапа — не для постоянного использования.
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    engine = create_async_engine(url, connect_args={"ssl": ctx})
    try:
        async with engine.begin() as conn:
            n = (await conn.execute(text("select count(*) from organizations"))).scalar()
            if int(n or 0) == 0:
                await conn.execute(
                    text(
                        "insert into organizations("
                        "id,name,slug,timezone,currency,"
                        "iiko_api_login,iiko_organization_id,iiko_terminal_group_id,whatsapp_phone_number_id,"
                        "prepayment_enforced,telegram_ops_chat_id,is_active"
                        ") values ("
                        "1,'RestoMind','default','UTC','KZT',"
                        "'','','','',"
                        "true,'',true"
                        ")"
                    )
                )
                print("inserted_default_org")
            else:
                print(f"orgs_exist={n}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

